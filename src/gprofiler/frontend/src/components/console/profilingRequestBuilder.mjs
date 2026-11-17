/*
 * Pure (React-free) helpers that translate selected inventory rows into the
 * payload accepted by POST /api/metrics/profile_request[/bulk].
 *
 * This module is intentionally dependency-free so the request-shaping "spec"
 * can be exercised by fast unit/acceptance tests (see
 * profilingRequestBuilder.test.mjs) without spinning up React or a browser.
 *
 * Contract (kept in sync with the backend ProfilingRequest validator):
 *   - stop_level is "host" for the coarse scopes (service, host) and "process"
 *     for the finer scopes; it is only set for stop requests.
 *   - A host-/service-level stop must NOT carry any PID (neither in
 *     target_hosts nor in target_entities), otherwise the backend rejects it.
 *   - Process-level identifiers (pid, process_name) are therefore only attached
 *     to target entities when the active scope is "process".
 */

// Scopes that resolve to whole-host targets (no per-process PIDs).
export const HOST_LEVEL_SCOPES = ['service', 'host'];

const CONTINUOUS_DURATION_SECONDS = 60;

/**
 * Build a single target entity descriptor for a selected row.
 *
 * Process-level identifiers (pid, process_name) are only included for the
 * "process" scope. Attaching them to coarser scopes is unnecessary for target
 * resolution and, for stop requests, trips the backend validation that forbids
 * PIDs when stop_level is "host".
 *
 * @param {object} row - Normalized inventory row.
 * @param {string} scope - Active scope (service|namespace|host|pod|container|process).
 * @returns {object} Target entity payload.
 */
export const buildTargetEntity = (row, scope) => {
    const entity = {
        id: row.id,
        service_name: row.service,
        namespace: row.namespace || undefined,
        hostname: row.host || undefined,
        ip_address: row.ip || undefined,
        pod_name: row.podName || undefined,
        container_name: row.containerName || undefined,
        workload_name: row.workloadName || undefined,
        workload_kind: row.workloadKind || undefined,
    };

    if (scope === 'process') {
        // Use a null check (not truthiness) so a legitimate PID of 0 is preserved.
        entity.pid = row.pid == null ? undefined : row.pid;
        entity.process_name = row.processName || undefined;
    }

    return entity;
};

/**
 * Resolve the stop_level for a request given the action and scope.
 * @returns {string|undefined} "host" | "process" for stops, undefined otherwise.
 */
export const resolveStopLevel = (action, scope) => {
    if (action !== 'stop') {
        return undefined;
    }
    return HOST_LEVEL_SCOPES.includes(scope) ? 'host' : 'process';
};

/**
 * Group selected rows by their service name (preserving selection order).
 * @param {Array<object>} selectedRows
 * @returns {Record<string, Array<object>>}
 */
export const groupRowsByService = (selectedRows) =>
    selectedRows.reduce((groups, row) => {
        if (!groups[row.service]) {
            groups[row.service] = [];
        }
        groups[row.service].push(row);
        return groups;
    }, {});

const appendDistinct = (params, key, values) => {
    [...new Set((values || []).filter(Boolean))].sort().forEach((value) => params.append(key, value));
};

/**
 * Query string for the nsys session dropdown.
 *
 * Checked rows win, and the scope decides which of their fields identify a
 * session: a service has no single host (the row's host is only a representative),
 * a namespace is the service plus namespace, and every finer scope runs on a host.
 * With nothing checked, the applied filter parameters narrow the list the same
 * way the table does. An empty result lists every fresh host.
 *
 * @param {string} scope
 * @param {Array<object>} selectedRows
 * @param {object} [appliedFilters]
 * @returns {URLSearchParams}
 */
export const buildNsysSessionQuery = (scope, selectedRows = [], appliedFilters = {}) => {
    const params = new URLSearchParams();
    if (selectedRows.length > 0) {
        if (scope === 'service') {
            appendDistinct(params, 'service_name', selectedRows.map((row) => row.service));
        } else if (scope === 'namespace') {
            appendDistinct(params, 'service_name', selectedRows.map((row) => row.service));
            appendDistinct(params, 'namespace', selectedRows.map((row) => row.namespace));
        } else {
            appendDistinct(params, 'hostname', selectedRows.map((row) => row.host));
        }
        return params;
    }
    if (appliedFilters.service) params.append('service_contains', appliedFilters.service);
    if (appliedFilters.hostname) params.append('hostname_contains', appliedFilters.hostname);
    if (appliedFilters.namespace) params.append('namespace_contains', appliedFilters.namespace);
    if (appliedFilters.podName) params.append('pod_contains', appliedFilters.podName);
    if (appliedFilters.ip) params.append('ip_contains', appliedFilters.ip);
    return params;
};

/**
 * Build the per-service profiling requests for a bulk start/stop action.
 *
 * @param {'start'|'stop'} action
 * @param {Array<object>} selectedRows - Normalized inventory rows.
 * @param {object} config
 * @param {string} config.scope - Active scope.
 * @param {'continuous'|'adhoc'|string} config.profilingMode
 * @param {number} config.duration
 * @param {number} config.profilingFrequency
 * @param {boolean} config.enablePerfSpect
 * @param {boolean} [config.enableNsys] - When true, agents attach to the nsys
 *   session named by nsysSession (nsys start / nsys stop). Adhoc mode is
 *   recommended by the UI; this builder does not force continuous=false.
 * @param {string} [config.nsysSession] - Session from `nsys sessions list`.
 *   Omitted when GPU capture is off or nothing is selected.
 * @param {boolean} [config.enableNsysTimeline] - With enableNsys, upload the CPU/GPU
 *   timeline view instead of the GPU flamegraph.
 * @param {boolean} [config.enableNsysTimelineStacks] - With the timeline, also record a
 *   CPU backtrace per kernel launch (heavier capture) for click-for-stack.
 * @param {boolean} [config.enableNsysUploadRep] - With enableNsys, also upload the raw
 *   .nsys-rep capture for download (openable in NVIDIA Nsight Systems; can be large).
 * @param {object} config.profilerConfigs
 * @param {number} config.maxProcesses
 * @returns {{grouped: Record<string, Array<object>>, requests: Array<object>}}
 */
export const buildProfilingRequests = (action, selectedRows, config) => {
    const {
        scope,
        profilingMode,
        duration,
        profilingFrequency,
        enablePerfSpect,
        enableNsys = false,
        enableNsysTimeline = false,
        enableNsysTimelineStacks = false,
        enableNsysUploadRep = false,
        nsysSession = '',
        profilerConfigs,
        maxProcesses,
    } = config;

    const grouped = groupRowsByService(selectedRows);
    const isContinuous = profilingMode === 'continuous';
    const stopLevel = resolveStopLevel(action, scope);

    const requests = Object.entries(grouped).map(([serviceName, serviceRows]) => {
        const targetHosts =
            scope === 'host'
                ? serviceRows.reduce((hostMap, row) => {
                      // null => whole-host target (no PIDs) per the backend contract.
                      hostMap[row.host] = null;
                      return hostMap;
                  }, {})
                : undefined;

        return {
            service_name: serviceName,
            request_type: action,
            continuous: isContinuous,
            duration: isContinuous ? CONTINUOUS_DURATION_SECONDS : duration,
            frequency: profilingFrequency,
            profiling_mode: 'cpu',
            target_scope: scope,
            target_hosts: targetHosts,
            target_entities: serviceRows.map((row) => buildTargetEntity(row, scope)),
            stop_level: stopLevel,
            additional_args: {
                enable_perfspect: enablePerfSpect,
                enable_nsys: enableNsys,
                // Timeline flags only make sense with nsys enabled; gate them so a
                // stale saved toggle can't reach the agent without the capture.
                nsys_timeline: enableNsys && enableNsysTimeline,
                nsys_timeline_stacks: enableNsys && enableNsysTimeline && enableNsysTimelineStacks,
                nsys_upload_rep: enableNsys && enableNsysUploadRep,
                nsys_capture_mode: 'launch',
                ...(enableNsys && nsysSession ? { nsys_session: nsysSession } : {}),
                profiler_configs: profilerConfigs,
                max_processes: maxProcesses,
            },
        };
    });

    return { grouped, requests };
};
