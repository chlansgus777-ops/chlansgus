import { beforeEach } from "vitest";
import { resetApiCache } from "./components/useApi";

// the screen cache is app-wide (module state): every test starts without another test's answers
beforeEach(() => resetApiCache());
