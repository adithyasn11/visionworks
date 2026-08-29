'use client';

// frontend/app/dashboard/employees/EmployeesScreen.jsx
//
// The employee roster: add, edit, deactivate, remove, and assign a desk.
//
// WHY THE DESK ASSIGNMENT IS THE POINT OF THIS SCREEN
//
// A name in a list does nothing on its own. The `assignedZoneId` is what turns
// this page into working identity tracking, because the seat prior is the
// strongest signal available in a fixed-desk office (85-95% alone) and the only
// one that needs no biometrics at all. Everything else here exists to make that
// one field correct and unambiguous.
//
// Ambiguity is prevented in the DATABASE, not here: `employees_one_active_per_zone`
// is a partial unique index, so two active employees physically cannot share a
// desk. This screen surfaces that as a readable sentence rather than being the
// thing that enforces it — if it were only enforced here, a direct POST to the
// Server Action would create exactly the ambiguity Step 7's binding rule cannot
// survive.
//
// THREE LAYERS, THE SAME AS EVERY OTHER SCREEN
//
//   1. this file   hide controls a VIEWER cannot use
//   2. actions.js  re-check inside every Server Action
//   3. Postgres    employee_insert/update + soft_delete_employee()
//
// Layer 3 is the one that holds. Measured on a real database: a VIEWER's insert
// is rejected, and an ADMIN of another organisation sees zero of these rows.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  ContactRound, Plus, Loader2, Pencil, Trash2, X, Check, ScanFace,
  MapPin, UserCheck, UserX, AlertTriangle,
} from 'lucide-react';

import DashboardShell from '../DashboardShell';
import { Field, Banner } from '../../components/AuthFormBits';
import { can, denialMessage } from '../../lib/permissions';
import {
  listEmployees, createEmployee, updateEmployee,
  setEmployeeActive, removeEmployee,
} from './actions';

/** Absolute date. Relative times ("2 days ago") go stale in an open tab. */
function formatDate(value) {
  if (!value) return null;
  try {
    return new Date(value).toLocaleDateString(undefined, {
      day: 'numeric', month: 'short', year: 'numeric',
    });
  } catch {
    return null;
  }
}

function initialsOf(employee) {
  return (employee.displayName || employee.employeeCode || '?')
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0].toUpperCase())
    .join('');
}

/* ── Status pill ─────────────────────────────────────────────────────────── */

function StatusPill({ active }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-bold ${
        active
          ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
          : 'border-[color:var(--line)] bg-surface-alt text-ink-muted'
      }`}
    >
      {active ? <UserCheck className="w-3 h-3" /> : <UserX className="w-3 h-3" />}
      {active ? 'Active' : 'Inactive'}
    </span>
  );
}

/* ── Desk picker ─────────────────────────────────────────────────────────── */

/**
 * Only WORKSTATION zones appear, and a desk already held by another ACTIVE
 * employee is disabled rather than hidden.
 *
 * Disabled-not-hidden is deliberate. A manager looking for "Desk 4" and not
 * finding it would assume the zone is missing; seeing it greyed out with the
 * holder's name tells them what to do next. The database would reject the write
 * either way — this only decides how the refusal reads.
 */
function DeskSelect({ id, value, onChange, zones, takenBy, disabled }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-[13px] font-bold text-ink">
        Desk zone
      </label>
      <select
        id={id}
        name={id}
        value={value ?? ''}
        onChange={onChange}
        disabled={disabled}
        className="w-full rounded-lg bg-ground border-2 border-field hover:border-field-hover px-3.5 py-2.5 text-[14px] text-ink transition-colors duration-150 focus:outline-none focus:border-[color:var(--accent)] focus:ring-2 focus:ring-[color:var(--accent-soft)] disabled:opacity-60"
      >
        <option value="">No desk assigned</option>
        {zones.map((z) => {
          const holder = takenBy.get(z.id);
          return (
            <option key={z.id} value={z.id} disabled={Boolean(holder)}>
              {z.name}{holder ? ` — taken by ${holder}` : ''}
            </option>
          );
        })}
      </select>
      <p className="text-[12px] text-ink-faint font-medium">
        {zones.length === 0
          ? 'No workstation zones exist yet. Draw one in Zones first — desk time cannot be attributed without it.'
          : 'The strongest identity signal, and the only one that needs no face data.'}
      </p>
    </div>
  );
}

/* ── Remove confirmation ─────────────────────────────────────────────────── */

/**
 * A confirm dialog is dismissed by reflex, so removal asks for the person's
 * name — the same bar /settings/organisation sets for deleting an org.
 */
function RemoveDialog({ employee, busy, onCancel, onConfirm }) {
  const [typed, setTyped] = useState('');
  const matches = typed.trim() === employee.displayName;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ground/80 backdrop-blur-md p-4">
      <div className="w-full max-w-md rounded-xl border border-line bg-surface p-5 themed">
        <h2 className="text-[16px] font-black tracking-tight text-ink mb-1.5 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-accent" />
          Remove {employee.displayName}?
        </h2>
        <p className="text-[13px] text-ink-muted font-medium leading-relaxed mb-4">
          They stop being tracked and their desk is freed. Recorded history is kept, so
          previous reports keep a name to point at. Type{' '}
          <span className="font-mono font-bold text-ink">{employee.displayName}</span> to confirm.
        </p>

        <Field
          id="confirmRemove"
          label="Employee name"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder={employee.displayName}
          autoComplete="off"
          maxLength={160}
        />

        <div className="flex items-center gap-2 mt-5">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="flex-1 px-3.5 py-2.5 rounded-lg border-2 border-field text-ink font-bold text-[14px] hover:border-field-hover transition-colors duration-150 disabled:opacity-60"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={!matches || busy}
            className="flex-1 flex items-center justify-center gap-2 px-3.5 py-2.5 rounded-lg bg-accent text-white font-bold text-[14px] hover:brightness-110 transition-[filter,opacity] duration-150 disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {busy && <Loader2 className="w-4 h-4 animate-spin" />}
            Remove
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── Screen ──────────────────────────────────────────────────────────────── */

export default function EmployeesScreen({ orgName, initialRole, viewer }) {
  const [state, setState] = useState({
    employees: [], zones: [], viewerRole: initialRole, loading: true,
  });
  const [banner, setBanner] = useState(null);
  const [busy, setBusy] = useState(null);       // id | 'create'
  const [editing, setEditing] = useState(null); // employee id
  const [removing, setRemoving] = useState(null);
  const [fieldError, setFieldError] = useState(null);

  const [form, setForm] = useState({ employeeCode: '', displayName: '', assignedZoneId: '' });
  const [editForm, setEditForm] = useState({ employeeCode: '', displayName: '', assignedZoneId: '' });

  const role = state.viewerRole ?? initialRole;
  const canEdit = can(role, 'employees.edit');

  const refresh = useCallback(async () => {
    const res = await listEmployees();
    if (!res.ok) {
      setState((s) => ({ ...s, loading: false }));
      setBanner({ kind: 'error', text: res.message });
      return;
    }
    setState({
      employees: res.employees,
      zones: res.zones,
      viewerRole: res.viewerRole,
      loading: false,
    });
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  /**
   * zoneId -> the ACTIVE employee holding it.
   *
   * Only ACTIVE employees are counted, matching the partial unique index
   * exactly: an inactive employee does NOT hold their desk, and the index does
   * not either. If this map disagreed with the index, the UI would grey out a
   * desk the database would happily accept, or offer one it would reject.
   */
  const takenBy = useMemo(() => {
    const m = new Map();
    for (const e of state.employees) {
      if (e.active && e.assignedZoneId) m.set(e.assignedZoneId, e.displayName);
    }
    return m;
  }, [state.employees]);

  /** The picker for a specific row must not grey out that row's OWN desk. */
  const takenByExcept = useCallback((employeeId) => {
    const m = new Map();
    for (const e of state.employees) {
      if (e.id !== employeeId && e.active && e.assignedZoneId) {
        m.set(e.assignedZoneId, e.displayName);
      }
    }
    return m;
  }, [state.employees]);

  const zoneName = useCallback(
    (id) => state.zones.find((z) => z.id === id)?.name ?? null,
    [state.zones],
  );

  const run = async (key, fn) => {
    setBusy(key);
    setBanner(null);
    try {
      const res = await fn();
      if (!res.ok) { setBanner({ kind: 'error', text: res.message }); return false; }
      setBanner({ kind: 'success', text: res.message });
      await refresh();
      return true;
    } catch {
      setBanner({ kind: 'error', text: 'Could not reach the server. Check your connection and try again.' });
      return false;
    } finally {
      setBusy(null);
    }
  };

  const submitCreate = async (e) => {
    e.preventDefault();
    setFieldError(null);
    if (!form.displayName.trim()) { setFieldError('A name is required.'); return; }
    if (!form.employeeCode.trim()) { setFieldError('An employee code is required.'); return; }

    const ok = await run('create', () => createEmployee(form));
    if (ok) setForm({ employeeCode: '', displayName: '', assignedZoneId: '' });
  };

  const startEdit = (emp) => {
    setEditing(emp.id);
    setEditForm({
      employeeCode: emp.employeeCode,
      displayName: emp.displayName,
      assignedZoneId: emp.assignedZoneId ?? '',
    });
  };

  const submitEdit = async (id) => {
    const ok = await run(id, () => updateEmployee(id, editForm));
    if (ok) setEditing(null);
  };

  const activeCount = state.employees.filter((e) => e.active).length;
  const seatedCount = state.employees.filter((e) => e.active && e.assignedZoneId).length;

  return (
    /* Routed mode: no `onViewChange`, so the section nav links back to the
       dashboard rather than switching view state in place. */
    <DashboardShell user={viewer} role={role}>
      <div className="mx-auto max-w-3xl">

        <header className="flex flex-col gap-2 mb-7">
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-accent">
            {orgName}
          </p>
          <h1 className="text-2xl sm:text-[30px] font-black tracking-tight text-ink leading-[1.15]">
            Employees
          </h1>
          <p className="text-[14px] text-ink-muted font-medium leading-relaxed max-w-xl">
            The people this system is permitted to identify by name. Assigning someone a desk
            is what lets their time be attributed to them — without it they are counted, but
            not named.
          </p>
        </header>

        {banner && (
          <div className="mb-5">
            <Banner kind={banner.kind}>{banner.text}</Banner>
          </div>
        )}

        {!canEdit && role && (
          <div className="mb-5 rounded-xl border border-line bg-surface-alt px-4 py-3">
            <p className="text-[13px] text-ink-muted font-medium leading-relaxed">
              {denialMessage('employees.edit')} You can see the roster because every member can,
              but changes are limited to administrators and managers.
            </p>
          </div>
        )}

        {/* Add. Gated on employees.edit, matching employee_insert. */}
        {canEdit && (
          <section className="mb-7 rounded-xl border border-line bg-surface p-4 sm:p-5">
            <h2 className="text-[15px] font-black tracking-tight text-ink mb-1 flex items-center gap-2">
              <Plus className="w-4 h-4 text-accent" />
              Add an employee
            </h2>
            <p className="text-[12.5px] text-ink-muted font-medium mb-4 leading-relaxed">
              The code is yours to choose — a staff number or initials. It only has to be
              unique inside this organisation, and it becomes reusable if they leave.
            </p>

            <form onSubmit={submitCreate} noValidate className="flex flex-col gap-4">
              <Field
                id="displayName"
                label="Name"
                value={form.displayName}
                onChange={(e) => { setForm({ ...form, displayName: e.target.value }); setFieldError(null); }}
                placeholder="Prajwal Kumar"
                autoComplete="off"
                maxLength={160}
                error={fieldError}
              />
              <Field
                id="employeeCode"
                label="Employee code"
                value={form.employeeCode}
                onChange={(e) => { setForm({ ...form, employeeCode: e.target.value }); setFieldError(null); }}
                placeholder="E-001"
                autoComplete="off"
                maxLength={64}
              />
              <DeskSelect
                id="assignedZoneId"
                value={form.assignedZoneId}
                onChange={(e) => setForm({ ...form, assignedZoneId: e.target.value })}
                zones={state.zones}
                takenBy={takenBy}
              />

              <button
                type="submit"
                disabled={busy === 'create'}
                className="self-start flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-accent text-white font-bold text-[14px] hover:brightness-110 transition-[filter,opacity] duration-150 disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {busy === 'create' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                Add employee
              </button>
            </form>
          </section>
        )}

        {/* Roster */}
        <section>
          <div className="flex items-baseline justify-between gap-3 mb-3">
            <h2 className="text-[15px] font-black tracking-tight text-ink flex items-center gap-2">
              <ContactRound className="w-4 h-4 text-accent" />
              Roster
            </h2>
            {!state.loading && state.employees.length > 0 && (
              <p className="font-mono text-[11px] text-ink-faint">
                {activeCount} active · {seatedCount} with a desk
              </p>
            )}
          </div>

          {state.loading ? (
            <div className="rounded-xl border border-line bg-surface px-4 py-10 flex items-center justify-center">
              <Loader2 className="w-5 h-5 animate-spin text-ink-faint" />
            </div>
          ) : state.employees.length === 0 ? (
            <div className="rounded-xl border border-line bg-surface px-4 py-8 text-center">
              <p className="text-[14px] font-bold text-ink mb-1">Nobody on the roster yet</p>
              <p className="text-[13px] text-ink-muted font-medium leading-relaxed max-w-sm mx-auto">
                {canEdit
                  ? 'Add the people who work in this space, then give each one their desk.'
                  : 'An administrator or manager has not added anyone yet.'}
              </p>
            </div>
          ) : (
            <ul className="flex flex-col gap-2">
              {state.employees.map((emp) => {
                const isEditing = editing === emp.id;
                const isBusy = busy === emp.id;
                const desk = zoneName(emp.assignedZoneId);

                return (
                  <li
                    key={emp.id}
                    className={`rounded-xl border border-line bg-surface p-4 ${emp.active ? '' : 'opacity-75'}`}
                  >
                    {isEditing ? (
                      <div className="flex flex-col gap-4">
                        <Field
                          id={`name-${emp.id}`}
                          label="Name"
                          value={editForm.displayName}
                          onChange={(e) => setEditForm({ ...editForm, displayName: e.target.value })}
                          maxLength={160}
                          autoComplete="off"
                        />
                        <Field
                          id={`code-${emp.id}`}
                          label="Employee code"
                          value={editForm.employeeCode}
                          onChange={(e) => setEditForm({ ...editForm, employeeCode: e.target.value })}
                          maxLength={64}
                          autoComplete="off"
                        />
                        <DeskSelect
                          id={`zone-${emp.id}`}
                          value={editForm.assignedZoneId}
                          onChange={(e) => setEditForm({ ...editForm, assignedZoneId: e.target.value })}
                          zones={state.zones}
                          takenBy={takenByExcept(emp.id)}
                        />
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => submitEdit(emp.id)}
                            disabled={isBusy}
                            className="flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-accent text-white font-bold text-[13px] hover:brightness-110 transition-[filter,opacity] duration-150 disabled:opacity-60"
                          >
                            {isBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                            Save
                          </button>
                          <button
                            type="button"
                            onClick={() => setEditing(null)}
                            disabled={isBusy}
                            className="flex items-center gap-1.5 px-3.5 py-2 rounded-lg border-2 border-field text-ink font-bold text-[13px] hover:border-field-hover transition-colors duration-150 disabled:opacity-60"
                          >
                            <X className="w-3.5 h-3.5" />
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="flex items-start gap-3">
                        <div className="w-9 h-9 shrink-0 rounded-full bg-accent-soft text-accent grid place-items-center font-black text-[12px]">
                          {initialsOf(emp)}
                        </div>

                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <Link
                            href={`/dashboard/employees/${emp.id}`}
                            className="text-[14px] font-black text-ink truncate hover:text-accent transition-colors"
                            title={`See ${emp.displayName}'s measured figures`}
                          >
                            {emp.displayName}
                          </Link>
                            <StatusPill active={emp.active} />
                          </div>

                          <p className="font-mono text-[11.5px] text-ink-faint mt-0.5">
                            {emp.employeeCode}
                            {formatDate(emp.createdAt) ? ` · added ${formatDate(emp.createdAt)}` : ''}
                          </p>

                          <p className="text-[12.5px] font-medium mt-1.5 flex items-center gap-1.5">
                            <MapPin className={`w-3.5 h-3.5 shrink-0 ${desk ? 'text-accent' : 'text-ink-faint'}`} />
                            {desk ? (
                              <span className="text-ink-muted">{desk}</span>
                            ) : (
                              /* Not a decorative absence — without a desk this person
                                 cannot be identified by the seat prior at all. */
                              <span className="text-ink-faint">
                                No desk assigned — cannot be identified by seat
                              </span>
                            )}
                          </p>
                        </div>

                        {canEdit && (
                          <div className="flex items-center gap-1 shrink-0">
                            {/* Face enrolment (Step 9). A routed page rather
                                than an inline panel: it needs the camera, and
                                a live video element inside a list row would
                                keep a webcam open behind whatever the user
                                scrolls to next. */}
                            <Link
                              href={`/dashboard/employees/${emp.id}/enroll`}
                              title="Enrol a face"
                              aria-label={`Enrol a face for ${emp.displayName}`}
                              className="p-2 rounded-lg text-ink-muted hover:text-ink hover:bg-surface-alt transition-colors duration-150"
                            >
                              <ScanFace className="w-4 h-4" />
                            </Link>
                            <button
                              type="button"
                              onClick={() => startEdit(emp)}
                              disabled={isBusy}
                              title="Edit"
                              aria-label={`Edit ${emp.displayName}`}
                              className="p-2 rounded-lg text-ink-muted hover:text-ink hover:bg-surface-alt transition-colors duration-150 disabled:opacity-60"
                            >
                              <Pencil className="w-4 h-4" />
                            </button>
                            <button
                              type="button"
                              onClick={() => run(emp.id, () => setEmployeeActive(emp.id, !emp.active))}
                              disabled={isBusy}
                              title={emp.active ? 'Deactivate' : 'Reactivate'}
                              aria-label={`${emp.active ? 'Deactivate' : 'Reactivate'} ${emp.displayName}`}
                              className="p-2 rounded-lg text-ink-muted hover:text-ink hover:bg-surface-alt transition-colors duration-150 disabled:opacity-60"
                            >
                              {isBusy
                                ? <Loader2 className="w-4 h-4 animate-spin" />
                                : emp.active ? <UserX className="w-4 h-4" /> : <UserCheck className="w-4 h-4" />}
                            </button>
                            <button
                              type="button"
                              onClick={() => setRemoving(emp)}
                              disabled={isBusy}
                              title="Remove"
                              aria-label={`Remove ${emp.displayName}`}
                              className="p-2 rounded-lg text-ink-muted hover:text-accent hover:bg-accent-soft transition-colors duration-150 disabled:opacity-60"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </div>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {/* What this page cannot tell you. Stated on the screen rather than
            only in the report, because a roster with desks looks more certain
            than it is. */}
        {!state.loading && state.employees.length > 0 && (
          <p className="mt-6 text-[12px] text-ink-faint font-medium leading-relaxed">
            Desk assignment identifies someone while they are at their own desk. Away from it
            they may be recorded as unattributed rather than guessed at — which is deliberate,
            and better than a confident wrong name.
          </p>
        )}
      </div>

      {removing && (
        <RemoveDialog
          employee={removing}
          busy={busy === removing.id}
          onCancel={() => setRemoving(null)}
          onConfirm={async () => {
            const ok = await run(removing.id, () => removeEmployee(removing.id));
            if (ok) setRemoving(null);
          }}
        />
      )}
    </DashboardShell>
  );
}
