import { describe, expect, it } from "vitest";
import { versionLabel } from "./App";
import type { SystemInfo } from "./types";

describe("installed version", () => {
  it("names the build commit so a reinstall can be checked", () => {
    const sys = { mode: "LIVE", mock_banner: false, now: "", versions: { app_version: "0.4.0", code_version: "d3d045b07d43" }, llm: { provider: "none", available: false, fast_model: "", deep_model: "" }, providers: [] } as SystemInfo;
    expect(versionLabel(sys)).toBe("앱 버전 0.4.0 · 빌드 d3d045b07d43");
  });
});
