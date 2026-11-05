/** A report contains fixed metadata only. An uncertain delivery keeps its original key. */
import { secureUuid } from "../uuid.mjs";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CATEGORIES = new Set(["HARASSMENT", "PRIVACY", "THREAT", "OTHER"]);
const failure = code => Object.assign(new Error(code), { code });

export class ReportClient {
  constructor({ request, csrfToken = () => "", uuid = secureUuid }) {
    this.request = request; this.csrfToken = csrfToken; this.uuid = uuid;
    this.intent = null; this.state = "IDLE"; this.inFlight = null; this.epoch = 0;
  }

  begin(category, submissionId) {
    if (!CATEGORIES.has(category) || submissionId !== undefined && !UUID.test(submissionId)) throw failure("INVALID_REPORT");
    const body = Object.freeze({ category, ...(submissionId ? { submissionId } : {}) });
    if (this.intent && ["SENDING", "UNKNOWN", "AUTH_REQUIRED"].includes(this.state)) {
      if (JSON.stringify(body) !== JSON.stringify(this.intent.body)) throw failure("PENDING_REPORT_EXISTS");
      return this.intent;
    }
    const idempotencyKey = this.uuid();
    if (!UUID.test(idempotencyKey)) throw failure("INVALID_IDEMPOTENCY_KEY");
    this.intent = Object.freeze({ body, idempotencyKey });
    this.state = "DRAFT";
    return this.intent;
  }

  send({ csrfToken } = {}) {
    if (!this.intent) throw failure("NO_REPORT_INTENT");
    if (this.inFlight) return this.inFlight;
    const intent = this.intent, epoch = this.epoch;
    this.state = "SENDING";
    this.inFlight = (async () => {
      try {
        const value = await this.request("/api/v2/reports", { method: "POST", body: intent.body,
          csrfToken: csrfToken ?? this.csrfToken(), idempotencyKey: intent.idempotencyKey });
        if (epoch !== this.epoch) return { state: "STALE" };
        if (!UUID.test(value?.reportId) || !["RECEIVED", "RESOLVED"].includes(value.status)) throw failure("INVALID_REPORT_RECEIPT");
        this.state = "ACCEPTED";
        return { state: "ACCEPTED", reportId: value.reportId, status: value.status };
      } catch (error) {
        if (epoch !== this.epoch) return { state: "STALE" };
        this.state = [401, 403].includes(error.status) ? "AUTH_REQUIRED"
          : error.status >= 400 && error.status < 500 ? "REJECTED" : "UNKNOWN";
        return { state: this.state, code: error.code || "RESULT_UNKNOWN" };
      } finally { if (epoch === this.epoch) this.inFlight = null; }
    })();
    return this.inFlight;
  }

  reset() { this.epoch++; this.intent = null; this.inFlight = null; this.state = "IDLE"; }
}
