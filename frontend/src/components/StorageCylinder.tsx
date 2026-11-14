const CYL_W = 100
const CYL_H = 132
const CYL_RX = 45
const CYL_RY = 12
const CYL_TOP = CYL_RY + 3
const CYL_BOTTOM = CYL_H - CYL_RY - 3
const CYL_LEFT = CYL_W / 2 - CYL_RX
const CYL_RIGHT = CYL_W / 2 + CYL_RX
const CYL_BANDS = [0.25, 0.5, 0.75] as const

function frontArc(y: number): string {
  return `M${CYL_LEFT} ${y} A${CYL_RX} ${CYL_RY} 0 0 0 ${CYL_RIGHT} ${y}`
}

function bodyPath(top: number): string {
  return `M${CYL_LEFT} ${top} V${CYL_BOTTOM} A${CYL_RX} ${CYL_RY} 0 0 0 ${CYL_RIGHT} ${CYL_BOTTOM} V${top} Z`
}

export function percentLabel(value: number | null): string {
  if (value == null) return '—'
  return `${value.toFixed(1).replace(/\.0$/, '')}%`
}

/** A banded storage cylinder. The blue part rises with the used percent; no percent leaves it empty. */
export function StorageCylinder({ percent, empty = false }: { percent: number | null; empty?: boolean }) {
  const level = percent == null || percent <= 0 ? 0 : Math.min(100, percent)
  const height = CYL_BOTTOM - CYL_TOP
  const fillTop = CYL_BOTTOM - (height * level) / 100
  const full = level >= 99.5
  const label = percent == null ? 'Used percent not returned' : `${percentLabel(percent)} used`
  return (
    <svg
      className={empty ? 'storage-cylinder storage-cylinder-empty' : 'storage-cylinder'}
      viewBox={`0 0 ${CYL_W} ${CYL_H}`}
      role="img"
      aria-label={label}
      focusable="false"
    >
      <path className="storage-cylinder-shell" d={bodyPath(CYL_TOP)} />
      {level > 0 && (
        <g className="storage-cylinder-fill" style={{ ['--fill-level' as string]: `${level}%` }}>
          <path d={bodyPath(fillTop)} />
          {!full && <ellipse className="storage-cylinder-surface" cx={CYL_W / 2} cy={fillTop} rx={CYL_RX} ry={CYL_RY} />}
        </g>
      )}
      {CYL_BANDS.map((share) => (
        <path key={share} className="storage-cylinder-band" d={frontArc(CYL_TOP + height * share)} />
      ))}
      <path className="storage-cylinder-cap storage-cylinder-cap-bottom" d={frontArc(CYL_BOTTOM)} />
      <path className="storage-cylinder-outline" d={`M${CYL_LEFT} ${CYL_TOP} V${CYL_BOTTOM} M${CYL_RIGHT} ${CYL_TOP} V${CYL_BOTTOM}`} />
      <ellipse
        className={full ? 'storage-cylinder-cap storage-cylinder-cap-top storage-cylinder-cap-full' : 'storage-cylinder-cap storage-cylinder-cap-top'}
        cx={CYL_W / 2}
        cy={CYL_TOP}
        rx={CYL_RX}
        ry={CYL_RY}
      />
    </svg>
  )
}
