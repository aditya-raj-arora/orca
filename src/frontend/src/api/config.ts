/**
 * Backend base URLs. In local dev these are unused — Vite's dev server proxy
 * (vite.config.ts) forwards relative /api, /ws paths to localhost:8000.
 * In production (Render static site, see docs/DEPLOYMENT.md), there is no
 * proxy, so wsClient.ts and any REST calls must use these absolute URLs
 * instead, set at build time via VITE_API_BASE_URL / VITE_WS_BASE_URL.
 *
 * Owner: P5.
 */
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "";
export const WS_BASE_URL: string = import.meta.env.VITE_WS_BASE_URL ?? "";
