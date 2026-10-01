import { useEffect, useRef, useState } from "react";
import { previewUrl, shouldReloadPreview } from "./camera";

/** The phone's greyscale preview (PROTOCOL.md §4.6). Reloads only when `preview_seq` changes, at most
 * PREVIEW_MAX_HZ, so a still phone costs no requests. */
export function PreviewImage({ cam, seq }: { cam: number; seq: number | null }) {
  const [shown, setShown] = useState<number | null>(null);
  const lastLoad = useRef<number | null>(null);

  useEffect(() => {
    const tick = () => {
      if (shouldReloadPreview(shown, seq, lastLoad.current, Date.now())) {
        lastLoad.current = Date.now();
        setShown(seq);
      }
    };
    tick();
    const t = setInterval(tick, 100);
    return () => clearInterval(t);
  }, [seq, shown]);

  if (shown === null) return <p className="muted">No preview from the phone yet.</p>;
  return <img className="preview" src={previewUrl(cam, shown)} alt={`Camera ${cam} preview`} />;
}
