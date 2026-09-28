import { describe, expect, it } from "vitest";
import license from "./assets/fonts/Pretendard-LICENSE.txt?raw";
import shipped from "../public/licenses/Pretendard-OFL-1.1.txt?raw";

// vitest blanks CSS imports: read the stylesheet itself (Node's fs in the test runner; no @types/node in this project)
// @ts-ignore
import { readFileSync } from "node:fs";
const css: string = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

describe("bundled font and tabular numbers (owner request 2026-09-28)", () => {
  it("loads Pretendard Variable from the app's own file, never from the network or the PC", () => {
    const face = css.match(/@font-face\s*{[^}]*}/)?.[0] ?? "";
    expect(face).toContain('font-family: "Pretendard Variable"');
    expect(face).toContain('url("./assets/fonts/PretendardVariable.woff2")');
    expect(css).not.toMatch(/url\(["']?https?:/); // no CDN
    expect(css).not.toMatch(/local\(/); // no reliance on an installed copy
    expect(css).toMatch(/:root\s*{[^}]*font-family: "Pretendard Variable"/);
  });
  it("ships the SIL Open Font License with the font (source and installed app)", () => {
    for (const t of [license, shipped]) {
      expect(t).toContain("SIL OPEN FONT LICENSE Version 1.1");
      expect(t).toContain("Reserved Font Name Pretendard");
    }
  });
  it("numbers use tabular figures everywhere", () => {
    expect(css).toMatch(/:root\s*{[^}]*font-variant-numeric: tabular-nums/);
    expect(css).toMatch(/svg text[^{]*{[^}]*tabular-nums/);
    expect(css).not.toContain("monospace"); // numbers keep the bundled font (a system monospace font would differ by PC)
  });
});
