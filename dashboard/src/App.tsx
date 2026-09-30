import { useState } from "react";
import { useServerState } from "./api/useServerState";
import { SystemBar } from "./components/SystemBar";
import { EventsPage } from "./pages/EventsPage";
import { LivePage } from "./pages/LivePage";
import { TagsPage } from "./pages/TagsPage";

type Tab = "live" | "tags" | "events";
const TABS: { id: Tab; label: string }[] = [
  { id: "live", label: "Live" },
  { id: "tags", label: "Tags" },
  { id: "events", label: "Events" },
];

export function App() {
  const s = useServerState();
  const [tab, setTab] = useState<Tab>("live");
  return (
    <div className="app">
      <SystemBar system={s.state?.system ?? null} connected={s.connected} mismatch={s.mismatch} />
      <nav className="tabs">
        {TABS.map((t) => (
          <button key={t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </nav>
      {!s.connected && <p className="warning">Not connected to the server. Retrying…</p>}
      <main>
        {tab === "live" && <LivePage state={s.state} lastEvents={s.lastEvents} />}
        {tab === "tags" && <TagsPage state={s.state} />}
        {tab === "events" && <EventsPage lastId={s.state?.system.events_last_id} />}
      </main>
    </div>
  );
}
