import Link from "next/link";
import { LadderPicker } from "@/components/Ladder";
import type { Meta } from "@/lib/api";
import { serverGet } from "@/lib/server";
import styles from "./home.module.css";

type Dash = {
  totals: { filed: number; open: number; resolved_or_closed: number };
  checkpoints: { day: string; root_hash: string; tickets: number; anchor_url: string | null }[];
};

export default async function Home() {
  const [meta, dash] = await Promise.all([serverGet<Meta>("/api/public/meta"), serverGet<Dash>("/api/public/dashboard")]);
  const cp = dash?.checkpoints?.[0];

  return (
    <>
      <section className={styles.hero}>
        <div>
          <h1 className={styles.headline}>A complaint here keeps climbing until someone answers it.</h1>
          <p className={styles.lede}>
            The clock starts the moment you file. If the person responsible misses the deadline, your complaint moves to
            the next authority on its own. Every step is sealed into a record that shows if anyone edits it later.
          </p>
          <div className="row">
            <Link className="btn" href="/file">
              File a complaint
            </Link>
            <Link className="btn secondary" href="/file?anonymous=1">
              File anonymously
            </Link>
          </div>
          <p className="small muted" style={{ marginTop: 14 }}>
            Filed anonymously before? <Link href="/track">Track it with your token</Link>.
          </p>
        </div>
        <div className={styles.ladderBox}>
          {meta ? <LadderPicker meta={meta} /> : <p className="muted">The server is not reachable right now.</p>}
        </div>
      </section>

      <section className={styles.points}>
        <div>
          <h3>Deadlines enforce themselves</h3>
          <p className="muted">
            Nobody has to chase an official. A background worker checks every deadline and moves overdue complaints up,
            even ones nobody picked up.
          </p>
        </div>
        <div>
          <h3>Edits leave a mark</h3>
          <p className="muted">
            Each step is hashed together with the one before it. Change or delete any step and the record no longer
            verifies. Officials are logged every time they open a complaint.
          </p>
        </div>
        <div>
          <h3>Anonymous means anonymous</h3>
          <p className="muted">
            Anonymous complaints are stored without your account, even if you are signed in. A one-time token is your only
            proof, and only its hash is kept.
          </p>
        </div>
      </section>

      {cp && (
        <section className={styles.seal}>
          <div>
            <h3>Today&apos;s seal</h3>
            <p className="muted small">
              A daily fingerprint of every complaint record, published so the whole history can be checked against it.
            </p>
          </div>
          <div>
            <div className="mono" style={{ wordBreak: "break-all" }}>
              {cp.root_hash}
            </div>
            <div className="small muted">
              {cp.day}, covers {cp.tickets} complaints.{" "}
              {cp.anchor_url ? <a href={cp.anchor_url}>Published copy</a> : <Link href="/record">See the public record</Link>}
            </div>
          </div>
        </section>
      )}
    </>
  );
}
