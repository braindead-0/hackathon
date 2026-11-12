import { useEffect, useState } from "react";
import { clinicalApi, type MobilePairing } from "./api";

export function MobileAlertPairing({ patientId }: { patientId: string }) {
  const [pairing, setPairing] = useState<(MobilePairing & { deadline: number }) | null>(null);
  const [status, setStatus] = useState<{ paired: number; connected: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const refresh = async () => {
      try {
        const next = await clinicalApi.mobileStatus(patientId);
        if (!cancelled) setStatus(next);
      } catch {
        if (!cancelled) setStatus(null);
      }
      if (!cancelled) {
        setNow(Date.now());
        timer = window.setTimeout(refresh, 3000);
      }
    };
    void refresh();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [patientId]);

  const generateCode = async () => {
    setBusy(true);
    setError(null);
    try {
      const next = await clinicalApi.pairMobile(patientId);
      setPairing({ ...next, deadline: Date.now() + next.expires_in * 1000 });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create pairing code");
    } finally {
      setBusy(false);
    }
  };

  const expires = pairing ? Math.max(0, Math.ceil((pairing.deadline - now) / 60000)) : 0;

  return (
    <div className="panel" style={{ padding: 14 }}>
      <div className="spread" style={{ gap: 12, flexWrap: "wrap" }}>
        <div>
          <strong className="small">VitalTrack phone alerts</strong>
          <div className="small muted" role="status">
            {status === null ? "Checking phone connection…" : status.connected > 0
              ? `${status.connected} phone${status.connected > 1 ? "s" : ""} connected — live alert sync`
              : status.paired > 0 ? "Phone paired but offline — reopen VitalTrack"
                : "Pair VitalTrack to ring and vibrate with your smartwatch alerts"}
          </div>
        </div>
        <button type="button" className="btn btn--small" onClick={generateCode} disabled={busy}>
          {busy ? "Creating code…" : pairing ? "New pairing code" : "Pair VitalTrack"}
        </button>
      </div>
      {pairing && (
        <div className="notice" style={{ marginTop: 12 }}>
          <div>In VitalTrack, open <strong>Profile → Patient alert sync</strong>.</div>
          <div className="small" style={{ marginTop: 8 }}>Server address (same Wi-Fi):</div>
          {pairing.server_urls.length ? pairing.server_urls.map((url) => (
            <div key={url} style={{ overflowWrap: "anywhere" }}><code>{url}</code></div>
          )) : <div className="small">No local network address found. Connect this computer to Wi-Fi.</div>}
          <div style={{ marginTop: 8 }}>Pairing code: <strong style={{ letterSpacing: "0.15em" }}>{expires ? pairing.code : "Expired"}</strong></div>
          <div className="small muted">{expires ? `Single use · expires in ${expires} min · keep this code private` : "Generate a new code to connect."}</div>
        </div>
      )}
      <div className="small muted" style={{ marginTop: 8 }}>
        Keep VitalTrack open with native alerts enabled. Background delivery is not guaranteed.
        Normal readings and sensor faults do not ring the phone.
      </div>
      {error && <div className="notice notice--warn" role="alert">{error}</div>}
    </div>
  );
}
