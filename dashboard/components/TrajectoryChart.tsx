import { Panel } from "./Panel";
import { LOCI, LOCUS_COLOUR, freq, strategyLabel, type Model } from "@/lib/model";

const W = 260;
const H = 110;
const PAD = 22;

function Spark({ history, generations }: { history: Model["strategy"]["trajectories"][string]["history"]; generations: number }) {
  const x = (g: number) => PAD + (g / Math.max(generations, 1)) * (W - PAD - 4);
  const y = (f: number) => H - PAD - f * (H - PAD - 8);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" role="img">
      <line x1={PAD} y1={y(0.5)} x2={W - 4} y2={y(0.5)} stroke="#556" strokeDasharray="3 3" strokeWidth="1" />
      <line x1={PAD} y1={y(0)} x2={W - 4} y2={y(0)} stroke="#334" strokeWidth="1" />
      <line x1={PAD} y1={y(0)} x2={PAD} y2={y(1)} stroke="#334" strokeWidth="1" />
      <text x={PAD - 4} y={y(1) + 4} textAnchor="end" fontSize="7" fill="#778">1</text>
      <text x={PAD - 4} y={y(0.5) + 3} textAnchor="end" fontSize="7" fill="#778">.5</text>
      <text x={PAD - 4} y={y(0) + 3} textAnchor="end" fontSize="7" fill="#778">0</text>
      {LOCI.map((l) => (
        <polyline
          key={l}
          fill="none"
          stroke={LOCUS_COLOUR[l]}
          strokeWidth="1.4"
          points={history.map((r) => `${x(r.generation).toFixed(1)},${y(freq(r, l)).toFixed(1)}`).join(" ")}
        />
      ))}
    </svg>
  );
}

export function TrajectoryChart({ model }: { model: Model }) {
  const { trajectories, config } = model.strategy;
  return (
    <Panel
      title="Allele frequency over generations"
      right={
        <span className="flex gap-2">
          {LOCI.map((l) => (
            <span key={l} className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-sm" style={{ background: LOCUS_COLOUR[l] }} />
              {l}
            </span>
          ))}
        </span>
      }
    >
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
        {Object.entries(trajectories).map(([name, t]) => (
          <figure key={name} className="min-w-0">
            <figcaption className="text-[10px] text-slate-400 mb-0.5">{strategyLabel(name)}</figcaption>
            <Spark history={t.history} generations={config.generations} />
          </figure>
        ))}
      </div>
      <p className="mt-2 text-[10px] text-slate-500">
        One representative seed per arm. The dashed line is the 0.5 threshold the comparison
        counts as resistance.
      </p>
    </Panel>
  );
}
