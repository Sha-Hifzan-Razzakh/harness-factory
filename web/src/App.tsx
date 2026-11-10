import { useState } from "react";
import { createHashRouter, NavLink, Outlet, RouterProvider } from "react-router-dom";
import { call, get, getToken, setToken } from "./api";
import { useLoad } from "./components/common";
import Activity from "./pages/Activity";
import Build from "./pages/Build";
import Dashboard from "./pages/Dashboard";
import Experiments from "./pages/Experiments";
import Home from "./pages/Home";
import Reference, { Cards } from "./pages/Reference";
import Results from "./pages/Results";
import Workspace from "./pages/Workspace";

const NAV: [string, string][] = [
  ["/", "Dashboard"], ["/experiments", "Experiments"], ["/build", "Build"], ["/workspace", "Workspace"],
  ["/activity", "Activity"], ["/results", "Results"], ["/cards", "Cards"], ["/reference", "What we can test with"],
  ["/guide", "Guide"],
];

function Layout() {
  const who = useLoad(() => get<{ author: string }>("/api/whoami"), []);
  return (
    <div className="shell">
      <nav className="nav">
        <div className="brand">Harness Factory</div>
        {NAV.map(([to, label]) => <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>{label}</NavLink>)}
        <div className="foot">
          <div>Signed changes as <b>{who.data?.author ?? "…"}</b></div>
          <div className="small">Set HF_AUTHOR in .env to change it.</div>
          <a href="/docs" target="_blank" rel="noreferrer">API docs</a>
        </div>
      </nav>
      <main className="main"><Outlet /></main>
    </div>
  );
}

const router = createHashRouter([{
  path: "/", element: <Layout />, children: [
    { index: true, element: <Dashboard /> },
    { path: "guide", element: <Home /> },
    { path: "experiments", element: <Experiments /> },
    { path: "experiments/:id", element: <Experiments /> },
    { path: "workspace", element: <Workspace /> },
    { path: "workspace/:kind/*", element: <Workspace /> },
    { path: "activity", element: <Activity /> },
    { path: "build", element: <Build /> },
    { path: "results", element: <Results /> },
    { path: "cards", element: <Cards /> },
    { path: "reference", element: <Reference /> },
  ],
}]);

/** The API may require HF_API_TOKEN: ask for it once, keep it in this browser. */
function Gate({ children }: { children: JSX.Element }) {
  const [tick, setTick] = useState(0);
  const probe = useLoad(() => call("GET", "/api/meta"), [tick]);
  const [value, setValue] = useState(getToken());
  if (probe.loading && !probe.data) return null;
  if (probe.error) return <div className="main"><div className="note bad">Can't reach the API: {probe.error}. Start it with <code>hfx web</code>.</div></div>;
  if (probe.data?.status === 401) {
    return (
      <div className="main" style={{ maxWidth: 520, margin: "60px auto" }}>
        <div className="panel stack">
          <h1>Harness Factory</h1>
          <div>This API needs its token (<code>HF_API_TOKEN</code> in your .env).</div>
          <input type="password" placeholder="token" value={value} onChange={(e) => setValue(e.target.value)} />
          <button className="primary" onClick={() => { setToken(value.trim()); setTick((t) => t + 1); }}>Continue</button>
          <div className="small muted">Kept in this browser only.</div>
        </div>
      </div>
    );
  }
  return children;
}

export default function App() {
  return <Gate><RouterProvider router={router} /></Gate>;
}
