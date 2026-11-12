import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { clinicalApi } from "./api";
import {
  buildDoctorPatientReport,
  buildPatientHealthReport,
  printReport,
  reportToHtml,
  reportToMarkdown,
  type DoctorNotes,
  type GeneratedReport,
} from "./reportGenerator";

const EMPTY_NOTES: DoctorNotes = { assessment: "", plan: "", followUp: "" };

export function ReportModal({
  report,
  onClose,
  onSaved,
}: {
  report: GeneratedReport;
  onClose: () => void;
  onSaved?: (key: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const [waPhone, setWaPhone] = useState("");
  const [waStatus, setWaStatus] = useState<string | null>(null);
  const [waSending, setWaSending] = useState(false);
  const [audience, setAudience] = useState<"doctor" | "patient">(
    report.kind === "patient-summary" ? "patient" : "doctor",
  );
  const [notes, setNotes] = useState<DoctorNotes>(report.doctorNotes ?? EMPTY_NOTES);
  const [saveStatus, setSaveStatus] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedKey, setSavedKey] = useState<string | null>(report.recordKey ?? null);

  const activeReport = useMemo(() => {
    const vc = report.visualContext;
    if (!vc || report.kind === "doctor-ward") return report;
    if (audience === "patient") {
      return buildPatientHealthReport(vc.state, vc.alerts, vc.readings, vc.liveOverride);
    }
    return buildDoctorPatientReport(vc.state, vc.alerts, vc.readings, vc.session);
  }, [audience, report]);

  const vc = activeReport.visualContext;
  const patientId = vc?.state.patient_id ?? null;
  const canEdit = audience === "doctor" && Boolean(patientId);
  const clinicianLabel = vc?.session
    ? `${vc.session.clinicianName} (${vc.session.clinicianId})`
    : "Attending Clinician";

  const displayReport = useMemo<GeneratedReport>(
    () => ({ ...activeReport, doctorNotes: notes, recordKey: savedKey ?? report.recordKey }),
    [activeReport, notes, savedKey, report.recordKey],
  );

  const storedOnce = useRef(false);
  useEffect(() => {
    if (!patientId || storedOnce.current) return;
    storedOnce.current = true;
    clinicalApi
      .storeReport({
        patient_id: patientId,
        title: displayReport.title,
        html: reportToHtml(displayReport),
      })
      .catch(() => {});
  }, [patientId, displayReport]);

  const handleDownload = () => {
    const md = reportToMarkdown(displayReport);
    const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = displayReport.filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleDownloadHtml = () => {
    const html = reportToHtml(displayReport);
    const blob = new Blob([html], { type: "text/html;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = displayReport.filename.replace(/\.md$/, ".html");
    a.click();
    URL.revokeObjectURL(url);
  };

  const handlePrint = () => {
    printReport(displayReport);
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(reportToMarkdown(displayReport));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      /* ignore clipboard error */
    }
  };

  const handleSave = async () => {
    if (!patientId) return;
    setSaving(true);
    setSaveStatus(null);
    const stamped: DoctorNotes = {
      ...notes,
      editedBy: clinicianLabel,
      editedAt: new Date().toISOString(),
    };
    try {
      const res = await clinicalApi.saveRecord({
        patient_id: patientId,
        report: { ...activeReport, doctorNotes: stamped },
      });
      setNotes(stamped);
      setSavedKey(res.key);
      setSaveStatus("Saved to patient record.");
      onSaved?.(res.key);
    } catch (e) {
      setSaveStatus(e instanceof Error ? e.message : "Failed to save report.");
    } finally {
      setSaving(false);
    }
  };

  const handleSendWhatsApp = async () => {
    setWaSending(true);
    setWaStatus("Sending report via WhatsApp...");
    const summary = reportToMarkdown(displayReport).slice(0, 1400);
    try {
      const res = await clinicalApi.sendWhatsappReport({
        title: displayReport.title,
        summary_text: summary,
        whatsapp_to: waPhone.trim() || null,
      });
      if (res.job_id) {
        for (let i = 0; i < 20; i += 1) {
          await new Promise((r) => window.setTimeout(r, 1000));
          const st = await clinicalApi.whatsappStatus(res.job_id);
          if (!st.queued) {
            setWaStatus(
              st.sent
                ? `Report sent via WhatsApp to ${st.recipients?.join(", ")}.`
                : `WhatsApp failed: ${st.error ?? "unknown error"}`,
            );
            break;
          }
        }
      }
    } catch (e) {
      setWaStatus(e instanceof Error ? e.message : "Failed to send report via WhatsApp");
    } finally {
      setWaSending(false);
    }
  };

  const mailtoHref = useMemo(() => {
    const subject = encodeURIComponent(displayReport.title);
    const body = encodeURIComponent(reportToMarkdown(displayReport).slice(0, 1800));
    return `mailto:?subject=${subject}&body=${body}`;
  }, [displayReport]);

  return createPortal(
    <div className="report-modal" role="dialog" aria-modal="true" aria-label={displayReport.title}>
      <div className="report-modal__backdrop" onClick={onClose} />
      <div className="report-modal__dialog">
        <div className="report-modal__toolbar">
          <div className="row row--tight" style={{ alignItems: "center", flexWrap: "wrap", gap: 10 }}>
            <span className="small muted" style={{ fontWeight: 600 }}>
              Report Preview
            </span>
            {vc && (
              <div className="seg" role="group" aria-label="Report audience">
                <button
                  type="button"
                  className={audience === "doctor" ? "seg__btn is-active" : "seg__btn"}
                  onClick={() => setAudience("doctor")}
                >
                  Doctor Copy
                </button>
                <button
                  type="button"
                  className={audience === "patient" ? "seg__btn is-active" : "seg__btn"}
                  onClick={() => setAudience("patient")}
                >
                  Patient Copy
                </button>
              </div>
            )}
          </div>
          <div className="row row--tight" style={{ flexWrap: "wrap", gap: 8, alignItems: "center" }}>
            <button className="btn btn--small" onClick={handleCopy}>
              {copied ? "Copied!" : "Copy Markdown"}
            </button>
            <button className="btn btn--small" onClick={handleDownload}>
              Download .md
            </button>
            <button className="btn btn--small" onClick={handleDownloadHtml}>
              Download HTML
            </button>
            <a className="btn btn--small" href={mailtoHref}>
              Email Summary
            </a>
            <div className="row row--tight" style={{ alignItems: "center", gap: 4 }}>
              <input
                type="tel"
                className="input"
                style={{ width: 140, padding: "5px 8px", fontSize: 12 }}
                placeholder="WhatsApp # (or .env)"
                value={waPhone}
                onChange={(e) => setWaPhone(e.target.value)}
              />
              <button
                className="btn btn--small btn--primary"
                onClick={handleSendWhatsApp}
                disabled={waSending}
              >
                {waSending ? "Sending..." : "Send via WhatsApp"}
              </button>
            </div>
            <button className="btn btn--small btn--primary" onClick={handlePrint}>
              Print / Save PDF
            </button>
            <button className="btn btn--small" onClick={onClose} aria-label="Close report">
              Close
            </button>
          </div>
        </div>
        {waStatus && (
          <div className="notice" style={{ margin: "0 20px 10px" }}>
            {waStatus}
          </div>
        )}

        <iframe
          title={displayReport.title}
          srcDoc={reportToHtml(displayReport)}
          style={{
            width: "100%",
            height: "70vh",
            border: "1px solid #cbd5e1",
            borderRadius: 8,
            background: "#fff",
          }}
        />

        {canEdit && (
          <section className="report-sheet__section" style={{ borderTop: "2px solid #bcd3e6", paddingTop: 14 }}>
            <div className="spread" style={{ alignItems: "center", marginBottom: 8 }}>
              <h3 style={{ margin: 0, border: "none" }}>Clinician addendum (editable)</h3>
              <div className="row row--tight" style={{ alignItems: "center", gap: 8 }}>
                {saveStatus && (
                  <span className="small" style={{ color: saveStatus.startsWith("Saved") ? "#166534" : "#991b1b" }}>
                    {saveStatus}
                  </span>
                )}
                <button className="btn btn--small btn--primary" onClick={handleSave} disabled={saving}>
                  {saving ? "Saving…" : savedKey ? "Save changes" : "Save to record"}
                </button>
              </div>
            </div>
            <p className="small muted" style={{ marginTop: 0 }}>
              Your assessment, plan, and follow-up are stored with this report and shown on the patient dashboard.
            </p>
            <label className="small" style={{ fontWeight: 600, display: "block", margin: "8px 0 4px" }}>
              Assessment
            </label>
            <textarea
              className="input"
              rows={3}
              placeholder="Clinical impression / interpretation of the flagged findings…"
              value={notes.assessment}
              onChange={(e) => setNotes((n) => ({ ...n, assessment: e.target.value }))}
            />
            <label className="small" style={{ fontWeight: 600, display: "block", margin: "8px 0 4px" }}>
              Plan
            </label>
            <textarea
              className="input"
              rows={3}
              placeholder="Management plan, medication changes, investigations…"
              value={notes.plan}
              onChange={(e) => setNotes((n) => ({ ...n, plan: e.target.value }))}
            />
            <label className="small" style={{ fontWeight: 600, display: "block", margin: "8px 0 4px" }}>
              Follow-up
            </label>
            <textarea
              className="input"
              rows={2}
              placeholder="Review interval, escalation criteria, next appointment…"
              value={notes.followUp}
              onChange={(e) => setNotes((n) => ({ ...n, followUp: e.target.value }))}
            />
          </section>
        )}
      </div>
    </div>,
    document.body,
  );
}