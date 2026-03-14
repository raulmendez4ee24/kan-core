"""
Motor de descubrimiento automático de APIs.
Escanea sitios web, documentación, y código para encontrar endpoints de API.
"""
import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from brain.openapi_parser import extract_endpoints_from_openapi, parse_openapi_document
from models import ApiIntegration

logger = logging.getLogger("kan_core.api_discovery")


class APIDiscoveryEngine:
    """Motor que descubre APIs automáticamente desde navegadores y documentación."""

    def __init__(self, session: AsyncSession, client_id: str):
        self.session = session
        self.client_id = client_id
        self._http_client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)

    async def discover_from_url(
        self,
        url: str,
        *,
        auto_encrypt: bool = True,
        max_depth: int = 3,
    ) -> Dict[str, Any]:
        """
        Descubre APIs desde una URL.
        Busca OpenAPI specs, documentación, y endpoints comunes.
        """
        discovered: List[Dict[str, Any]] = []
        visited = set()

        async def _crawl(url_path: str, depth: int) -> None:
            if depth > max_depth or url_path in visited:
                return
            visited.add(url_path)

            try:
                # Intentar encontrar OpenAPI spec
                openapi_urls = [
                    urljoin(url_path, "/openapi.json"),
                    urljoin(url_path, "/openapi.yaml"),
                    urljoin(url_path, "/swagger.json"),
                    urljoin(url_path, "/api-docs"),
                    urljoin(url_path, "/docs/openapi.json"),
                    urljoin(url_path, "/v1/openapi.json"),
                ]

                for spec_url in openapi_urls:
                    try:
                        resp = await self._http_client.get(spec_url)
                        if resp.status_code == 200:
                            content_type = resp.headers.get("content-type", "").lower()
                            if "json" in content_type or spec_url.endswith(".json"):
                                spec = resp.json()
                            else:
                                try:
                                    import yaml  # type: ignore[import-not-found]
                                    spec = yaml.safe_load(resp.text)  # type: ignore[union-attr]
                                except ImportError:
                                    logger.warning("PyYAML no instalado, solo se soportan specs JSON")
                                    continue
                            
                            base_url, endpoints = extract_endpoints_from_openapi(spec, max_endpoints=50)
                            if endpoints:
                                discovered.append({
                                    "source": "openapi",
                                    "url": spec_url,
                                    "base_url": base_url or url_path,
                                    "endpoints": [e.model_dump() for e in endpoints],
                                    "openapi_spec": spec,
                                })
                                logger.info(f"Descubierta API OpenAPI desde {spec_url}")
                                return
                    except Exception as e:
                        logger.debug(f"No se encontró spec en {spec_url}: {e}")
                    except Exception as e:
                        logger.debug(f"No se encontró spec en {spec_url}: {e}")

                # Buscar en HTML por patrones de API
                try:
                    resp = await self._http_client.get(url_path)
                    if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", "").lower():
                        html_content = resp.text
                        
                        # Buscar URLs de API en el HTML
                        api_patterns = [
                            r'["\']([^"\']*\/api\/[^"\']*)["\']',
                            r'["\']([^"\']*\/v\d+\/[^"\']*)["\']',
                            r'baseURL["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                            r'apiUrl["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                        ]
                        
                        found_urls = set()
                        for pattern in api_patterns:
                            matches = re.findall(pattern, html_content, re.IGNORECASE)
                            for match in matches:
                                full_url = urljoin(url_path, match)
                                if full_url not in visited and depth < max_depth:
                                    found_urls.add(full_url)
                        
                        # Buscar documentación de API
                        doc_patterns = [
                            r'["\']([^"\']*\/docs?\/[^"\']*)["\']',
                            r'["\']([^"\']*\/documentation[^"\']*)["\']',
                            r'["\']([^"\']*\/api-docs?[^"\']*)["\']',
                        ]
                        
                        for pattern in doc_patterns:
                            matches = re.findall(pattern, html_content, re.IGNORECASE)
                            for match in matches:
                                doc_url = urljoin(url_path, match)
                                if doc_url not in visited:
                                    await _crawl(doc_url, depth + 1)
                        
                        # Intentar descubrir endpoints comunes
                        common_endpoints = [
                            "/api",
                            "/api/v1",
                            "/api/v2",
                            "/rest",
                            "/graphql",
                        ]
                        
                        for endpoint in common_endpoints:
                            test_url = urljoin(url_path, endpoint)
                            if test_url not in visited:
                                try:
                                    test_resp = await self._http_client.get(test_url)
                                    if test_resp.status_code in (200, 401, 403):
                                        # Parece un endpoint de API
                                        discovered.append({
                                            "source": "html_scan",
                                            "url": test_url,
                                            "base_url": test_url,
                                            "endpoints": [],
                                            "status_code": test_resp.status_code,
                                        })
                                except Exception:
                                    pass
                except Exception as e:
                    logger.debug(f"Error procesando HTML de {url_path}: {e}")

            except Exception as e:
                logger.warning(f"Error descubriendo desde {url_path}: {e}")

        await _crawl(url, 0)

        result = {
            "discovered_apis": discovered,
            "total_found": len(discovered),
        }

        if auto_encrypt and discovered:
            await self._save_discovered_apis(discovered)

        return result

    async def discover_from_browser(
        self,
        browser_controller: Any,
        *,
        target_url: str,
        auto_encrypt: bool = True,
    ) -> Dict[str, Any]:
        """
        Descubre APIs desde un navegador activo.
        Analiza requests de red, documentación visible, y código JavaScript.
        """
        discovered: List[Dict[str, Any]] = []
        
        try:
            # Navegar a la URL
            await browser_controller.browser_goto(
                url=target_url,
                wait_until="networkidle",
            )

            # Extraer información de la página
            page_content = await browser_controller.browser_extract(
                selector="body",
                all_matches=False,
            )
            
            # Buscar en el código fuente
            # browser_extract retorna un dict con "value" (texto) o "text" dependiendo del atributo
            page_source = page_content.get("value", "") or page_content.get("text", "")
            
            # Buscar URLs de API en JavaScript
            js_api_patterns = [
                r'fetch\(["\']([^"\']+)["\']',
                r'axios\.(get|post|put|delete)\(["\']([^"\']+)["\']',
                r'\.ajax\([^,]*url["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                r'apiUrl["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                r'baseURL["\']?\s*[:=]\s*["\']([^"\']+)["\']',
            ]
            
            found_apis = set()
            for pattern in js_api_patterns:
                matches = re.findall(pattern, page_source, re.IGNORECASE)
                for match in matches:
                    if isinstance(match, tuple):
                        api_url = match[-1] if match else None
                    else:
                        api_url = match
                    
                    if api_url:
                        full_url = urljoin(target_url, api_url)
                        if any(x in full_url.lower() for x in ["/api/", "/v1/", "/v2/", "/rest/", "/graphql"]):
                            found_apis.add(full_url)
            
            # Buscar documentación visible
            doc_selectors = [
                'a[href*="docs"]',
                'a[href*="api"]',
                'a[href*="documentation"]',
            ]
            
            for selector in doc_selectors:
                try:
                    links = await browser_controller.browser_extract(
                        selector=selector,
                        all_matches=True,
                        attribute="href",
                    )
                    if links.get("values"):
                        for link in links["values"]:
                            if isinstance(link, str):
                                doc_url = urljoin(target_url, link)
                                discovery = await self.discover_from_url(doc_url, auto_encrypt=False)
                                if discovery.get("discovered_apis"):
                                    discovered.extend(discovery["discovered_apis"])
                except Exception:
                    pass
            
            # Agregar APIs encontradas
            for api_url in found_apis:
                discovered.append({
                    "source": "browser_js",
                    "url": api_url,
                    "base_url": api_url.rsplit("/", 1)[0] if "/" in api_url else api_url,
                    "endpoints": [],
                })
            
        except Exception as e:
            logger.error(f"Error descubriendo desde navegador: {e}")

        result = {
            "discovered_apis": discovered,
            "total_found": len(discovered),
        }

        if auto_encrypt and discovered:
            await self._save_discovered_apis(discovered)

        return result

    async def _save_discovered_apis(self, discovered: List[Dict[str, Any]]) -> None:
        """Guarda las APIs descubiertas en la base de datos con encriptación."""
        for api_info in discovered:
            try:
                base_url = api_info.get("base_url", "")
                if not base_url:
                    continue
                
                # Determinar nombre de integración
                parsed = urlparse(base_url)
                integration_name = parsed.netloc.replace(".", "_").replace("-", "_")
                if not integration_name:
                    integration_name = "discovered_api"
                
                # Verificar si ya existe
                from sqlalchemy import select
                stmt = select(ApiIntegration).where(
                    ApiIntegration.client_id == self.client_id,
                    ApiIntegration.name == integration_name,
                )
                existing = (await self.session.execute(stmt)).scalars().first()
                
                if existing:
                    # Actualizar existente
                    existing.base_url = base_url
                    existing.openapi_spec = api_info.get("openapi_spec")
                    existing.openapi_url = api_info.get("url") if api_info.get("source") == "openapi" else None
                    existing.docs_url = api_info.get("url")
                else:
                    # Crear nuevo
                    integration = ApiIntegration(
                        client_id=self.client_id,
                        name=integration_name,
                        base_url=base_url,
                        auth_type="none",
                        openapi_spec=api_info.get("openapi_spec"),
                        openapi_url=api_info.get("url") if api_info.get("source") == "openapi" else None,
                        docs_url=api_info.get("url"),
                        integration_metadata={
                            "discovery_source": api_info.get("source"),
                            "discovered_endpoints": api_info.get("endpoints", []),
                        },
                    )
                    self.session.add(integration)
                
                await self.session.commit()
                logger.info(f"API guardada: {integration_name} desde {base_url}")
                
            except Exception as e:
                logger.error(f"Error guardando API descubierta: {e}")
                await self.session.rollback()

    async def discover_from_terminal(
        self,
        command: str,
        *,
        auto_encrypt: bool = True,
    ) -> Dict[str, Any]:
        """
        Descubre APIs ejecutando comandos en terminal.
        Útil para encontrar APIs en código fuente, configuraciones, etc.
        """
        import subprocess
        
        discovered: List[Dict[str, Any]] = []
        
        try:
            # Ejecutar comando y capturar salida
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            
            output = result.stdout + result.stderr
            
            # Buscar URLs de API en la salida
            url_pattern = r'https?://[^\s<>"\'\)]+(?:/api|/v\d+|/rest|/graphql)'
            matches = re.findall(url_pattern, output, re.IGNORECASE)
            
            for url in matches:
                discovered.append({
                    "source": "terminal",
                    "url": url,
                    "base_url": url.rsplit("/", 1)[0] if "/" in url else url,
                    "endpoints": [],
                })
            
            # Buscar archivos de configuración comunes
            config_patterns = [
                r'["\']?api[_-]?url["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                r'["\']?base[_-]?url["\']?\s*[:=]\s*["\']([^"\']+)["\']',
            ]
            
            for pattern in config_patterns:
                matches = re.findall(pattern, output, re.IGNORECASE)
                for match in matches:
                    discovered.append({
                        "source": "terminal_config",
                        "url": match,
                        "base_url": match,
                        "endpoints": [],
                    })
                    
        except Exception as e:
            logger.error(f"Error descubriendo desde terminal: {e}")

        result = {
            "discovered_apis": discovered,
            "total_found": len(discovered),
        }

        if auto_encrypt and discovered:
            await self._save_discovered_apis(discovered)

        return result

    async def close(self) -> None:
        """Cierra recursos."""
        await self._http_client.aclose()
