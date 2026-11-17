"""Minimal Shopify Admin GraphQL client.

Every query/mutation here was checked against shopify.dev (Admin GraphQL API 2026-10, Nov 2026):
productCreate(product:, media:), productUpdate(product:, media:), productVariantsBulkUpdate,
publishablePublish, publications, stagedUploadsCreate, and the client-credentials token endpoint.

Authentication (two options):
1. Dev Dashboard app (current method): SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET. We exchange them for
   an access token at https://{shop}.myshopify.com/admin/oauth/access_token (lasts 24 hours, cached).
2. An older "legacy custom app" Admin API token: SHOPIFY_ACCESS_TOKEN (doesn't expire).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import httpx


class ShopifyError(RuntimeError):
    pass


class ShopifyNotConfigured(ShopifyError):
    pass


@dataclass
class _Token:
    value: str
    expires_at: float


class ShopifyClient:
    def __init__(self, shop: str, api_version: str, *, client_id: str | None = None, client_secret: str | None = None,
                 access_token: str | None = None, timeout: float = 30, http: httpx.Client | None = None,
                 sleep=time.sleep):
        shop = (shop or "").strip().lower().removesuffix(".myshopify.com").removeprefix("https://")
        if not shop:
            raise ShopifyNotConfigured("Set your store's subdomain in config.yaml → store.shop (e.g. \"my-store\").")
        if not access_token and not (client_id and client_secret):
            raise ShopifyNotConfigured("Add SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET to .env (see README → Connect Shopify).")
        self.shop = shop
        self.base = f"https://{shop}.myshopify.com"
        self.endpoint = f"{self.base}/admin/api/{api_version}/graphql.json"
        self.client_id, self.client_secret = client_id, client_secret
        self._token = _Token(access_token, float("inf")) if access_token else None
        self.http = http or httpx.Client(timeout=timeout)
        self.sleep = sleep

    # ---------- auth ----------
    def token(self) -> str:
        if self._token and time.time() < self._token.expires_at - 60:
            return self._token.value
        resp = self.http.post(f"{self.base}/admin/oauth/access_token",
                              data={"grant_type": "client_credentials", "client_id": self.client_id,
                                    "client_secret": self.client_secret},
                              headers={"Content-Type": "application/x-www-form-urlencoded"})
        if resp.status_code != 200:
            hint = ""
            if "shop_not_permitted" in resp.text:
                hint = (" The app and the store must be in the same organisation in the Dev Dashboard "
                        "(create the dev store from the Dev Dashboard's 'Dev stores' page).")
            raise ShopifyError(f"Couldn't get a Shopify access token ({resp.status_code}): {resp.text[:200]}{hint}")
        body = resp.json()
        self._token = _Token(body["access_token"], time.time() + float(body.get("expires_in", 86399)))
        return self._token.value

    # ---------- GraphQL ----------
    def graphql(self, query: str, variables: dict | None = None, *, retries: int = 4) -> dict:
        for attempt in range(retries + 1):
            resp = self.http.post(self.endpoint, json={"query": query, "variables": variables or {}},
                                  headers={"X-Shopify-Access-Token": self.token(), "Content-Type": "application/json"})
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < retries:
                    self.sleep(min(2 ** attempt, 10))  # rate limited / server busy: back off and retry
                    continue
            if resp.status_code == 401:
                raise ShopifyError("Shopify rejected the access token (401). Check your app's credentials.")
            if resp.status_code == 403:
                raise ShopifyError("Shopify refused (403): the app is missing an access scope. It needs "
                                   "write_products and write_publications.")
            if resp.status_code != 200:
                raise ShopifyError(f"Shopify returned {resp.status_code}: {resp.text[:300]}")
            body = resp.json()
            errors = body.get("errors")
            if errors:
                throttled = any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors)
                if throttled and attempt < retries:
                    self.sleep(min(2 ** attempt, 10))
                    continue
                raise ShopifyError("Shopify error: " + "; ".join(e.get("message", str(e)) for e in errors))
            return body["data"]
        raise ShopifyError("Shopify kept rate-limiting the request; try again in a minute.")

    @staticmethod
    def _check(payload: dict, what: str) -> dict:
        errors = payload.get("userErrors") or []
        if errors:
            raise ShopifyError(f"{what}: " + "; ".join(f"{'.'.join(e.get('field') or [])} {e['message']}".strip()
                                                       for e in errors))
        return payload

    # ---------- operations ----------
    def shop_name(self) -> str:
        return self.graphql("query { shop { name } }")["shop"]["name"]

    def create_product(self, product: dict, media: list[dict]) -> dict:
        q = """mutation productCreate($product: ProductCreateInput!, $media: [CreateMediaInput!]) {
          productCreate(product: $product, media: $media) {
            product { id handle status variants(first: 1) { nodes { id } } }
            userErrors { field message }
          }
        }"""
        p = self._check(self.graphql(q, {"product": product, "media": media or None})["productCreate"], "Create product")
        return p["product"]

    def update_product(self, product: dict, media: list[dict]) -> dict:
        q = """mutation productUpdate($product: ProductUpdateInput!, $media: [CreateMediaInput!]) {
          productUpdate(product: $product, media: $media) {
            product { id handle status variants(first: 1) { nodes { id } } }
            userErrors { field message }
          }
        }"""
        p = self._check(self.graphql(q, {"product": product, "media": media or None})["productUpdate"], "Update product")
        return p["product"]

    def update_variant(self, product_id: str, variant: dict) -> None:
        q = """mutation productVariantsBulkUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
          productVariantsBulkUpdate(productId: $productId, variants: $variants) {
            productVariants { id price }
            userErrors { field message }
          }
        }"""
        self._check(self.graphql(q, {"productId": product_id, "variants": [variant]})["productVariantsBulkUpdate"],
                    "Set price")

    def publication_id(self, name: str) -> str:
        q = "query { publications(first: 50) { nodes { id catalog { title } } } }"
        nodes = self.graphql(q)["publications"]["nodes"]
        for n in nodes:
            if ((n.get("catalog") or {}).get("title") or "").strip().lower() == name.strip().lower():
                return n["id"]
        titles = [(n.get("catalog") or {}).get("title") for n in nodes]
        raise ShopifyError(f"No '{name}' sales channel found (found: {', '.join(t for t in titles if t) or 'none'}). "
                           f"Set config.yaml → store.publication_name.")

    def publish(self, product_id: str, publication_id: str) -> None:
        q = """mutation publishablePublish($id: ID!, $input: [PublicationInput!]!) {
          publishablePublish(id: $id, input: $input) { userErrors { field message } }
        }"""
        self._check(self.graphql(q, {"id": product_id, "input": [{"publicationId": publication_id}]})["publishablePublish"],
                    "Publish")

    def upload_image(self, path: Path, mime: str) -> str:
        """Upload a local image via a staged upload; returns the resourceUrl to use as media originalSource."""
        q = """mutation stagedUploadsCreate($input: [StagedUploadInput!]!) {
          stagedUploadsCreate(input: $input) {
            stagedTargets { url resourceUrl parameters { name value } }
            userErrors { field message }
          }
        }"""
        data = path.read_bytes()
        target = self._check(self.graphql(q, {"input": [{
            "resource": "IMAGE", "filename": path.name, "mimeType": mime, "httpMethod": "POST",
            "fileSize": str(len(data))}]})["stagedUploadsCreate"], "Prepare image upload")["stagedTargets"][0]
        resp = self.http.post(target["url"], data={p["name"]: p["value"] for p in target["parameters"]},
                              files={"file": (path.name, data, mime)})
        if resp.status_code not in (200, 201, 204):
            raise ShopifyError(f"Image upload failed ({resp.status_code}).")
        return target["resourceUrl"]
