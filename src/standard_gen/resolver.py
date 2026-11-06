from __future__ import annotations

import json as _json
import re
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
import yaml as _yaml

from .models import TestResource

logger = logging.getLogger(__name__)


class Resolver:
    """
    Handles URI derivation, bblocks:// link resolution, local URL → file path
    mapping, and tested-by resolution.
    """

    def __init__(
        self,
        metadata,                        # StandardMetadata
        local_register: dict,            # parsed local register.json
        imported_registers: list[dict],  # parsed imported register entries (all bblocks combined)
        source_dir: Path,                # repo root (maps to baseURL)
    ):
        self._meta = metadata
        self._base_url: str = local_register.get('baseURL', '')
        if self._base_url and not self._base_url.endswith('/'):
            self._base_url += '/'
        self._source_dir = source_dir

        # Index all local bblocks by identifier
        self._local_bblocks: dict[str, dict] = {
            bb['itemIdentifier']: bb
            for bb in local_register.get('bblocks', [])
        }
        # Index all imported bblocks by identifier
        self._imported_bblocks: dict[str, dict] = {
            bb['itemIdentifier']: bb
            for bb in imported_registers
        }

    # ------------------------------------------------------------------
    # URI derivation
    # ------------------------------------------------------------------

    def _status_token(self) -> str:
        return 'draft/' if self._meta.status.lower() == 'draft' else ''

    def _expand(self, template: str, ident: str) -> str:
        return (
            template
            .replace('{base-uri}', self._meta.base_uri)
            .replace('{status}', self._status_token())
            .replace('{id}', ident)
        )

    def req_uri(self, req_id: str) -> str:
        return self._expand(self._meta.req_uri_template, req_id)

    def statement_uri(self, kind: str, stmt_id: str) -> str:
        """URI of a requirement, recommendation or permission."""
        template = {
            'recommendation': self._meta.rec_uri_template,
            'permission': self._meta.per_uri_template,
        }.get(kind, self._meta.req_uri_template)
        return self._expand(template, stmt_id)

    def conf_uri(self, class_id: str) -> str:
        return self._expand(self._meta.conf_uri_template, class_id)

    def ats_uri(self, req_uri: str) -> str:
        return req_uri.replace('/req/', '/ats/')

    def abstract_test_uri(self, test_id: str) -> str:
        return self._expand(self._meta.ats_uri_template, test_id)

    def display_id(self, uri: str) -> str:
        """
        How a statement or test identifier is shown: relative to the
        standard's base URI in the 'ogc' layout (e.g. /req/core/x), else as is.
        """
        base = self._meta.base_uri.rstrip('/')
        if self._meta.ogc_layout and uri.startswith(base + '/'):
            return uri[len(base):]
        return uri

    # ------------------------------------------------------------------
    # Local URL → filesystem path
    # ------------------------------------------------------------------

    def local_path(self, url: str) -> Optional[Path]:
        """Return local filesystem path for a URL belonging to the local build, or None."""
        if self._base_url and url.startswith(self._base_url):
            rel = url[len(self._base_url):]
            return self._source_dir / rel
        return None

    def fetch(self, url: str) -> str:
        """Fetch content from a URL, using local filesystem when possible."""
        local = self.local_path(url)
        if local is not None:
            logger.debug("Reading local file %s for URL %s", local, url)
            return local.read_text(encoding='utf-8')
        logger.debug("Fetching remote URL %s", url)
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.text

    # ------------------------------------------------------------------
    # bblocks:// link resolution
    # ------------------------------------------------------------------

    def resolve_bblocks_link(self, identifier: str) -> str:
        """Resolve a bblocks:// identifier to an in-document anchor or external URL."""
        if identifier in self._local_bblocks:
            return f'#bb-{_slugify(identifier)}'
        bb = self._imported_bblocks.get(identifier)
        if bb:
            viewer = bb.get('documentation', {}).get('bblocks-viewer', {}).get('url')
            if viewer:
                return viewer
        logger.warning("Could not resolve bblocks:// identifier: %s", identifier)
        return f'bblocks://{identifier}'

    def resolve_bblocks_links_in_html(self, html: str) -> str:
        """Replace all href="bblocks://..." in rendered HTML."""
        def replacer(m):
            identifier = m.group(1)
            resolved = self.resolve_bblocks_link(identifier)
            return f'href="{resolved}"'
        return re.sub(r'href="bblocks://([^"]+)"', replacer, html)

    # ------------------------------------------------------------------
    # tested-by resolution
    # ------------------------------------------------------------------

    def resolve_tested_by(
        self,
        tested_by: list[str],
        bb_test_resources: list[TestResource],
    ) -> list[TestResource]:
        """
        Resolve tested-by values (id or filename) to TestResource objects.
        Resolution order: id match first, then filename match.
        If tested_by is empty, returns all bb_test_resources.
        """
        if not tested_by:
            return list(bb_test_resources)

        by_id = {tr.id: tr for tr in bb_test_resources if tr.id}
        by_filename = {Path(tr.url).name: tr for tr in bb_test_resources}

        result = []
        for ref in tested_by:
            if ref in by_id:
                result.append(by_id[ref])
            elif ref in by_filename:
                result.append(by_filename[ref])
            else:
                # ref may be a filename without extension — try matching
                match = next(
                    (tr for tr in bb_test_resources
                     if Path(tr.url).stem == ref),
                    None,
                )
                if match:
                    result.append(match)
                else:
                    logger.warning("Could not resolve tested-by reference: %s", ref)
        return result

    # ------------------------------------------------------------------
    # Bblock lookup helpers
    # ------------------------------------------------------------------

    def get_bblock(self, identifier: str) -> Optional[dict]:
        return self._local_bblocks.get(identifier) or self._imported_bblocks.get(identifier)

    def get_bblock_viewer_url(self, identifier: str) -> Optional[str]:
        bb = self.get_bblock(identifier)
        if bb:
            return bb.get('documentation', {}).get('bblocks-viewer', {}).get('url')
        return None

    def get_bblock_schema(self, identifier: str) -> Optional[dict]:
        bb = self.get_bblock(identifier)
        if not bb:
            return None
        schema_map = bb.get('schema', {})
        url = schema_map.get('application/json') or schema_map.get('application/yaml')
        if not url:
            return None
        try:
            content = self.fetch(url)
            if url.endswith('.json'):
                return _json.loads(content)
            return _yaml.safe_load(content)
        except Exception as e:
            logger.warning("Could not fetch schema for %s: %s", identifier, e)
            return None

    def get_bblock_ontology(self, identifier: str) -> Optional[str]:
        """Turtle text of the bblock's ontology, or None if it has none."""
        bb = self.get_bblock(identifier)
        url = bb.get('ontology') if bb else None
        if not url:
            return None
        try:
            return self.fetch(url)
        except Exception as e:
            logger.warning("Could not fetch ontology for %s: %s", identifier, e)
            return None


def _slugify(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')
