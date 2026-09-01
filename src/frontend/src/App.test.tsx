/**
 * Smoke test — keeps CI green from Day 1. Owner: P6 (QA/Integration Lead).
 * Expand real component coverage as ChatPanel/MapPanel/TraceViewer land
 * (see their TODO comments for behaviour to test).
 */
import { describe, expect, it } from "vitest";

describe("sanity", () => {
  it("test runner is wired up", () => {
    expect(1 + 1).toBe(2);
  });
});
