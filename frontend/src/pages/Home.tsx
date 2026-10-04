import { useViewMode } from "../components/viewMode";
import BriefHome from "./BriefHome";
import Dashboard from "./Dashboard";

/** 홈: 간략(the decision in one look) or 자세히(the full dashboard), as the viewer chose in the menu. */
export default function Home() {
  const [mode] = useViewMode();
  return mode === "brief" ? <BriefHome /> : <Dashboard />;
}
