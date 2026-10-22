"use client";

import { useState } from "react";
import type { Meta } from "@/lib/api";
import { hoursLabel } from "@/lib/format";
import styles from "./Ladder.module.css";

/**
 * The escalation path for one category, drawn as a staircase. With `current`
 * it becomes a progress view for a single ticket.
 */
export function LadderView({
  ladder,
  current,
  ombudsperson,
  scale = 1,
}: {
  ladder: { level: number; designation: string; sla_hours: number }[];
  current?: number;
  ombudsperson?: boolean;
  scale?: number;
}) {
  return (
    <ol className={styles.ladder}>
      {ladder.map((step) => {
        const state =
          current === undefined ? "" : step.level < current ? styles.past : step.level === current && !ombudsperson ? styles.here : "";
        return (
          <li key={step.level} className={`${styles.step} ${state}`} style={{ ["--i" as string]: step.level - 1 }}>
            <span className={styles.level}>{step.level}</span>
            <span>
              <strong>{step.designation}</strong>
              <span className={styles.sla}>{hoursLabel(step.sla_hours * scale)} to act</span>
            </span>
          </li>
        );
      })}
      <li
        className={`${styles.step} ${styles.ombud} ${ombudsperson ? styles.here : ""}`}
        style={{ ["--i" as string]: ladder.length }}
      >
        <span className={styles.level} aria-hidden="true">
          !
        </span>
        <span>
          <strong>External ombudsperson</strong>
          <span className={styles.sla}>Alerted if the top authority misses its deadline. The breach is made public.</span>
        </span>
      </li>
    </ol>
  );
}

export function LadderPicker({ meta }: { meta: Meta }) {
  const [cat, setCat] = useState(meta.categories[0] ?? "Hostel");
  return (
    <div>
      <label className="field" style={{ maxWidth: 280 }}>
        <span>See the path for</span>
        <select value={cat} onChange={(e) => setCat(e.target.value)}>
          {meta.categories.map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
      </label>
      <LadderView ladder={meta.ladders[cat] ?? []} />
      <p className="small muted" style={{ marginTop: 16 }}>
        Times are for medium priority. High priority halves them, critical quarters them.
      </p>
    </div>
  );
}
