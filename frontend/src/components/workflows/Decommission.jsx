import React, { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../../api'
import { formatBytes } from '../../utils'
import { useToast } from '../Toast'
import {
  WorkflowHeader, EmptyState, LoadingRow, WorkflowError,
  ActionButton, Spinner, SpinKeyframes,
} from './shared'

const ACCENT = 'var(--red)'

function itemKey(item) {
  const lib = item.library || {}
  return `${lib.service || ''}:${lib.connection_id || ''}:${lib.arr_id ?? ''}`
}

function CandidateRow({ item, onOpen }) {
  const lib = item.library || {}
  const parsed = item.parsed || {}
  const title = lib.title || parsed.title || 'Unknown title'
  const filename = (item.rep_path || '').replace(/\\/g, '/').split('/').pop()

  return (
    <div style={{
      display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', gap: 16,
      alignItems: 'center', padding: '14px 16px', borderBottom: '1px solid var(--border)',
    }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 13.5, fontWeight: 650, color: 'var(--text)' }}>
            {title}{lib.year ? ` (${lib.year})` : ''}
          </span>
          <span style={{
            fontSize: 10, fontFamily: 'var(--mono)', padding: '2px 6px', borderRadius: 4,
            color: lib.service === 'radarr' ? 'var(--yellow)' : 'var(--blue)',
            background: lib.service === 'radarr' ? 'var(--yellow)14' : 'var(--blue)14',
            border: `1px solid ${lib.service === 'radarr' ? 'var(--yellow)' : 'var(--blue)'}35`,
          }}>{lib.service}</span>
          <span style={{ fontSize: 10.5, fontFamily: 'var(--mono)', color: 'var(--text-dim)' }}>
            connection {lib.connection_id} · ID {lib.arr_id}
          </span>
        </div>
        <div title={item.rep_path} style={{
          marginTop: 5, fontSize: 10.5, fontFamily: 'var(--mono)', color: 'var(--text-dim)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {filename || item.rep_path || 'No torrent path'}
        </div>
        <div style={{ marginTop: 6, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', fontSize: 10.5, color: 'var(--text-dim)' }}>
          <span>{item.file_count || 1} torrent file{item.file_count === 1 ? '' : 's'}</span>
          <span>{formatBytes(item.total_size || 0)}</span>
          {item.trackers?.length > 0 && <span>{item.trackers.join(', ')}</span>}
          {lib.arr_url && (
            <a href={lib.arr_url} target="_blank" rel="noopener noreferrer"
              style={{ color: 'var(--accent)', textDecoration: 'none' }}>
              Inspect in {lib.service} ↗
            </a>
          )}
        </div>
      </div>
      <ActionButton danger onClick={() => onOpen(item)}>Plan decommission</ActionButton>
    </div>
  )
}

export default function Decommission({ onNavigate }) {
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const toast = useToast()

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    api.decommissionReport()
      .then(setReport)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const open = useCallback(item => {
    setResult(null)
    setSelected(item)
  }, [])

  const close = useCallback(() => {
    if (busy) return
    setSelected(null)
    setResult(null)
    load()
  }, [busy, load])

  const confirm = async plan => {
    if (!selected) return
    const lib = selected.library || {}
    setBusy(true)
    try {
      const response = await api.decommission({
        confirmed: true,
        target: {
          service: lib.service,
          connection_id: lib.connection_id,
          arr_id: lib.arr_id,
          tmdb_id: lib.tmdb_id,
          tvdb_id: lib.tvdb_id,
          title: lib.title,
          torrent: selected.hash
            ? { hash: selected.hash, instance_id: selected.instance_id }
            : null,
        },
        plan,
      })
      if (response.status === 'partial') {
        setResult(response)
        toast(`Decommission finished with partial failures: ${(response.failed_components || []).join(', ')}`, 'error')
      } else {
        toast(`Decommissioned ${lib.title || 'item'}${response.audit_started ? ' · fresh audit started' : ''}`, 'success')
        const key = itemKey(selected)
        setReport(current => ({
          ...(current || {}),
          items: (current?.items || []).filter(item => itemKey(item) !== key),
          count: Math.max(0, (current?.count || 1) - 1),
        }))
        setSelected(null)
      }
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const items = report?.items || []

  return (
    <div style={{ padding: '28px 32px 64px', maxWidth: 1180 }}>
      <WorkflowHeader
        title="Decommission"
        blurb="Explicitly retire media you intentionally removed from Plex. Every title is a confirmation-only plan against one exact Sonarr or Radarr item; a missing file never runs this workflow automatically."
        accent={ACCENT}
        right={!loading && <ActionButton onClick={load}>Refresh candidates</ActionButton>}
      />

      <div style={{
        marginTop: 18, padding: '12px 14px', borderRadius: 8,
        background: 'var(--red)0b', border: '1px solid var(--red)30',
        fontSize: 12, lineHeight: 1.55, color: 'var(--text)',
      }}>
        <strong style={{ color: ACCENT }}>Intentional deletion only.</strong>{' '}
        Candidates come from Import Pending evidence and appear here only when Auditorr has both the configured Arr connection ID and exact Arr item ID. Opening a plan does nothing until you confirm it.
      </div>

      <div style={{ marginTop: 22 }}>
        {loading && <LoadingRow label="Finding exact-ID decommission candidates…" />}
        {!loading && error && <WorkflowError message={error} />}
        {!loading && !error && items.length === 0 && (
          <EmptyState
            emoji="✓"
            title="No decommission candidates"
            sub="There are no Import Pending items with an exact configured Sonarr/Radarr identity. Run a fresh audit after intentionally deleting media from Plex, then return here."
          />
        )}
        {!loading && !error && items.length > 0 && (
          <>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 9 }}>
              <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)' }}>Ready for review</span>
              <span style={{ fontSize: 11, fontFamily: 'var(--mono)', color: 'var(--text-dim)' }}>
                {items.length} exact target{items.length === 1 ? '' : 's'}
              </span>
            </div>
            <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 9, overflow: 'hidden', boxShadow: 'var(--elev-1)' }}>
              {items.map(item => <CandidateRow key={itemKey(item)} item={item} onOpen={open} />)}
            </div>
            {report?.truncated && (
              <div style={{ marginTop: 10, fontSize: 11, color: 'var(--yellow)' }}>
                The underlying audit candidate set was truncated. Resolve visible items or run another audit to reveal more.
              </div>
            )}
          </>
        )}
      </div>

      {!report?.client_delete_allowed && !loading && (
        <div style={{ marginTop: 16, fontSize: 11.5, color: 'var(--text-dim)' }}>
          Torrent removal choices are disabled.{' '}
          <button type="button" onClick={() => onNavigate?.({ tab: 'config' })}
            style={{ padding: 0, border: 0, background: 'none', color: 'var(--yellow)', cursor: 'pointer', textDecoration: 'underline' }}>
            Enable Workflow torrent deletion in Config
          </button>
          {' '}if you want those choices available.
        </div>
      )}

      {selected && (
        <DecommissionModal
          key={itemKey(selected)}
          item={selected}
          clientName={report?.client_name || 'torrent client'}
          clientDeleteAllowed={!!report?.client_delete_allowed}
          busy={busy}
          result={result}
          onCancel={close}
          onConfirm={confirm}
        />
      )}
      <SpinKeyframes />
    </div>
  )
}

function DecommissionModal({
  item, clientName, clientDeleteAllowed,
  busy, result, onCancel, onConfirm,
}) {
  const lib = item.library || {}
  const [arrAction, setArrAction] = useState('remove')
  const [addExclusion, setAddExclusion] = useState(true)
  const [deleteArrFiles, setDeleteArrFiles] = useState(false)
  const [torrentAction, setTorrentAction] = useState('keep')
  const dialogRef = useRef(null)
  const title = lib.title || item.parsed?.title || 'this item'

  useEffect(() => {
    const dialog = dialogRef.current
    const first = dialog?.querySelector('input:not([disabled]), button:not([disabled])')
    first?.focus()
    const onKey = e => {
      if (e.key === 'Escape' && !busy) { e.preventDefault(); onCancel(); return }
      if (e.key !== 'Tab' || !dialog) return
      const focusable = [...dialog.querySelectorAll('input:not([disabled]), button:not([disabled]), a[href]')]
      if (!focusable.length) return
      const firstEl = focusable[0]
      const lastEl = focusable[focusable.length - 1]
      if (e.shiftKey && document.activeElement === firstEl) { e.preventDefault(); lastEl.focus() }
      else if (!e.shiftKey && document.activeElement === lastEl) { e.preventDefault(); firstEl.focus() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onCancel])

  const confirm = () => onConfirm({
    arr_action: arrAction,
    add_import_exclusion: addExclusion,
    delete_arr_files: arrAction === 'remove' && deleteArrFiles,
    torrent_action: torrentAction,
  })

  const radioRow = (name, value, current, onChange, label, detail, disabled = false) => (
    <label style={{ display: 'flex', alignItems: 'flex-start', gap: 9, opacity: disabled ? 0.45 : 1, cursor: disabled ? 'not-allowed' : 'pointer' }}>
      <input type="radio" name={name} value={value} checked={current === value}
        disabled={disabled || busy || !!result} onChange={() => onChange(value)} style={{ marginTop: 3 }} />
      <span>
        <span style={{ display: 'block', fontSize: 12.5, color: 'var(--text)', fontWeight: 600 }}>{label}</span>
        {detail && <span style={{ display: 'block', fontSize: 11, color: 'var(--text-dim)', lineHeight: 1.45, marginTop: 2 }}>{detail}</span>}
      </span>
    </label>
  )
  const checkRow = (checked, onChange, label, detail, disabled = false) => (
    <label style={{ display: 'flex', alignItems: 'flex-start', gap: 9, opacity: disabled ? 0.45 : 1, cursor: disabled ? 'not-allowed' : 'pointer' }}>
      <input type="checkbox" checked={checked} disabled={disabled || busy || !!result}
        onChange={e => onChange(e.target.checked)} style={{ marginTop: 3 }} />
      <span>
        <span style={{ display: 'block', fontSize: 12.5, color: 'var(--text)', fontWeight: 600 }}>{label}</span>
        {detail && <span style={{ display: 'block', fontSize: 11, color: 'var(--text-dim)', lineHeight: 1.45, marginTop: 2 }}>{detail}</span>}
      </span>
    </label>
  )

  return createPortal(
    <div onMouseDown={e => { if (e.target === e.currentTarget && !busy) onCancel() }}
      style={{ position: 'fixed', inset: 0, zIndex: 210, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,.62)' }}>
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="decommission-title" aria-describedby="decommission-desc"
        style={{ width: 'min(650px, calc(100vw - 40px))', maxHeight: 'calc(100vh - 56px)', overflowY: 'auto', background: 'var(--surface)', border: '1px solid var(--border2)', borderRadius: 12, boxShadow: '0 18px 70px rgba(0,0,0,.55)' }}>
        <div style={{ padding: '19px 21px 0' }}>
          <h2 id="decommission-title" style={{ margin: 0, fontSize: 16, color: ACCENT }}>Decommission {title}</h2>
          <p id="decommission-desc" style={{ margin: '8px 0 0', fontSize: 12, lineHeight: 1.55, color: 'var(--text-dim)' }}>
            Confirmation-only action for the exact {lib.service} item on connection <span style={{ fontFamily: 'var(--mono)' }}>{lib.connection_id}</span>, ID <span style={{ fontFamily: 'var(--mono)' }}>{lib.arr_id}</span>.
            {' '}Auditorr will re-check this identity live before doing anything. Nothing here runs merely because a file is missing.
          </p>
        </div>

        {!result ? (
          <div style={{ padding: '16px 21px', display: 'flex', flexDirection: 'column', gap: 15 }}>
            <fieldset style={modalFieldset}>
              <legend style={modalLegend}>Arr item</legend>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {radioRow('arr-action', 'remove', arrAction, setArrAction, `Remove from ${lib.service}`, 'Removes the item by exact live Arr ID. Remaining Arr-side files are kept unless separately enabled below.')}
                {radioRow('arr-action', 'keep_unmonitored', arrAction, setArrAction, 'Keep in Arr, unmonitored', 'Leaves the item in place but prevents monitoring and automatic reacquisition.')}
                {checkRow(addExclusion, setAddExclusion, 'Add Arr import-list exclusion', 'Prevents an import list from adding the title back after decommissioning.')}
                {checkRow(deleteArrFiles, setDeleteArrFiles, 'Delete remaining Arr-side files or extras', 'Default OFF. This asks Arr to delete any files it still owns.', arrAction !== 'remove')}
              </div>
            </fieldset>

            <fieldset style={modalFieldset}>
              <legend style={modalLegend}>Torrent</legend>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {radioRow('torrent-action', 'keep', torrentAction, setTorrentAction, 'Keep seeding', 'Default. Auditorr does not contact the torrent client.')}
                {radioRow('torrent-action', 'remove_registration', torrentAction, setTorrentAction, `Remove registration from ${clientName}`, 'Keeps every torrent payload file.', !clientDeleteAllowed || !item.hash)}
                {radioRow('torrent-action', 'remove_auto', torrentAction, setTorrentAction, 'Remove with cross-seed-safe file handling', 'Uses Auditorr’s existing delete_files="auto" partition: shared live payloads stay; unshared links can be deleted.', !clientDeleteAllowed || !item.hash)}
                {!clientDeleteAllowed && <div style={{ fontSize: 11, color: 'var(--yellow)' }}>Torrent removal is unavailable because Workflow torrent deletion is disabled in Config.</div>}
              </div>
            </fieldset>

            <div style={{ padding: '10px 12px', borderRadius: 7, background: 'var(--red)0d', border: '1px solid var(--red)30', color: 'var(--text)', fontSize: 11.5, lineHeight: 1.5 }}>
              Review every choice. Components run independently, so the result can be partial and will be recorded with the live Arr payload captured before deletion.
            </div>
          </div>
        ) : (
          <div aria-live="polite" style={{ padding: '16px 21px' }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: result.status === 'partial' ? 'var(--yellow)' : 'var(--green)', marginBottom: 10 }}>
              {result.message}
            </div>
            <div style={{ border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
              {Object.entries(result.components || {}).map(([name, component]) => (
                <div key={name} style={{ display: 'grid', gridTemplateColumns: '145px 75px 1fr', gap: 8, padding: '8px 10px', borderBottom: '1px solid var(--border)', fontSize: 11.5 }}>
                  <span style={{ fontFamily: 'var(--mono)', color: 'var(--text)' }}>{name.replaceAll('_', ' ')}</span>
                  <span style={{ fontFamily: 'var(--mono)', color: component.status === 'failed' ? 'var(--red)' : component.status === 'success' ? 'var(--green)' : 'var(--text-dim)' }}>{component.status}</span>
                  <span style={{ color: 'var(--text-dim)' }}>{component.message}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '13px 21px 18px', borderTop: '1px solid var(--border)' }}>
          {busy && <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center', fontSize: 12, color: 'var(--text-dim)' }}><Spinner /> Executing confirmed plan…</span>}
          <span style={{ flex: 1 }} />
          {result ? (
            <ActionButton onClick={onCancel}>Close and refresh</ActionButton>
          ) : (
            <>
              <ActionButton onClick={onCancel} disabled={busy}>Cancel</ActionButton>
              <ActionButton danger onClick={confirm} disabled={busy}>{busy ? 'Working…' : 'Confirm decommission'}</ActionButton>
            </>
          )}
        </div>
      </div>
    </div>,
    document.body
  )
}

const modalFieldset = {
  margin: 0, padding: '12px 13px', border: '1px solid var(--border)', borderRadius: 8,
  background: 'var(--surface2)',
}
const modalLegend = { padding: '0 6px', fontSize: 12, fontWeight: 700, color: 'var(--text)' }
