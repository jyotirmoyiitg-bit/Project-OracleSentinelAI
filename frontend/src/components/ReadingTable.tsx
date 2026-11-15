const PERCENT = /^(.+?)\s+(\d+(?:\.\d+)? percent used\b.*)$/i
const NAMED = /^([^:]{1,80}):\s+(.+)$/

/**
 * Reading lines as a table. A percent-used line splits into name and detail.
 * With `named`, a "Database: finding" line splits at the first colon, so fleet answers read one database per row.
 */
export function ReadingTable({ lines, named = false }: { lines: string[]; named?: boolean }) {
  return (
    <div className="copilot-table-wrap">
      <table className={named ? 'copilot-table copilot-reading-table copilot-fleet-table' : 'copilot-table copilot-reading-table'}>
        <tbody>
          {lines.map((line, index) => {
            const match = named ? NAMED.exec(line) : PERCENT.exec(line)
            if (match) {
              const parts = named ? match[2].split('; ') : [match[2]]
              return (
                <tr key={index}>
                  <th scope="row">{match[1]}</th>
                  <td>{parts.length > 1 ? <ul className="copilot-finding-list">{parts.map((part, at) => <li key={at}>{part}</li>)}</ul> : parts[0]}</td>
                </tr>
              )
            }
            return <tr key={index}><td className="copilot-reading-line" colSpan={2}>{line}</td></tr>
          })}
        </tbody>
      </table>
    </div>
  )
}
