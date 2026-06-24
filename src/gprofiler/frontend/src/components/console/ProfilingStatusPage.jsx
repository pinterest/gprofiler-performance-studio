import {
    Alert,
    Box,
    Button,
    Chip,
    Dialog,
    DialogActions,
    DialogContent,
    DialogTitle,
    Divider,
    Snackbar,
    Tab,
    Tabs,
    Typography,
} from '@mui/material';
import queryString from 'query-string';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useHistory, useLocation } from 'react-router-dom';

import { DATA_URLS } from '../../api/urls';
import { PAGES } from '../../utils/consts';
import Icon from '../common/icon/Icon';
import { ICONS_NAMES } from '../common/icon/iconsData';
import MuiTable from '../common/dataDisplay/table/MuiTable';
import ProfilingHeader from './header/ProfilingHeader';
import ProfilingTopPanel from './header/ProfilingTopPanel';

const DEFAULT_PROFILING_FREQUENCY = 11;
const DEFAULT_MAX_PROCESSES = 10;
const DEFAULT_DURATION = 60;
const CONFIG_STORAGE_KEY = 'gprofiler.adhocProfilingConfig';

const SCOPES = [
    { id: 'service', label: 'Services' },
    { id: 'namespace', label: 'Namespaces' },
    { id: 'host', label: 'Hosts' },
    { id: 'pod', label: 'Pods' },
    { id: 'container', label: 'Containers' },
    { id: 'process', label: 'Processes' },
];

const EMPTY_FILTERS = {
    service: '',
    hostname: '',
    pids: '',
    ip: '',
    namespace: '',
    podName: '',
    containerName: '',
    processName: '',
    commandType: '',
    status: '',
};

const readField = (row, camelKey, snakeKey = camelKey) => row[camelKey] ?? row[snakeKey];

const formatHeartbeat = (value) => {
    if (!value) {
        return 'N/A';
    }

    try {
        let utcTimestamp = value;
        if (!utcTimestamp.endsWith('Z') && !utcTimestamp.includes('+') && !utcTimestamp.includes('-', 10)) {
            utcTimestamp += 'Z';
        }
        return new Date(utcTimestamp).toLocaleString(navigator.language, {
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: true,
        });
    } catch (error) {
        return 'Invalid date';
    }
};

const buildProfileUrl = (host, service, view) => {
    const baseUrl = `${window.location.protocol}//${window.location.host}`;
    const params = {
        filter: `hn,is,${host}`,
        gtab: '1',
        pm: '1',
        rtms: '1',
        service,
        time: '1h',
        view,
        wp: '100',
    };
    return `${baseUrl}${PAGES.profiles.to}?${new URLSearchParams(params).toString()}`;
};

const getScopeColumns = (scope) => {
    const sharedTimestampColumn = {
        field: 'heartbeatTimestamp',
        headerName: 'last heartbeat',
        flex: 1,
        sortable: true,
        renderCell: (params) => formatHeartbeat(params.value),
    };
    const sharedStatusColumns = [
        { field: 'profilingStatus', headerName: 'profiling', flex: 1, sortable: true },
        { field: 'profilingMode', headerName: 'mode', flex: 1, sortable: true },
        { field: 'profilerSummary', headerName: 'profilers', flex: 1.3, sortable: false },
        { field: 'frequency', headerName: 'frequency', flex: 0.8, sortable: true },
    ];

    if (scope === 'service') {
        return [
            { field: 'service', headerName: 'service name', flex: 1.4, sortable: true },
            { field: 'namespaceCount', headerName: 'namespaces', flex: 0.8, sortable: true },
            { field: 'hostCount', headerName: 'hosts', flex: 0.7, sortable: true },
            { field: 'podCount', headerName: 'pods', flex: 0.7, sortable: true },
            { field: 'containerCount', headerName: 'containers', flex: 0.8, sortable: true },
            { field: 'processCount', headerName: 'processes', flex: 0.8, sortable: true },
            ...sharedStatusColumns,
            sharedTimestampColumn,
            { field: 'agentVersion', headerName: 'version', flex: 0.8, sortable: true },
        ];
    }

    if (scope === 'namespace') {
        return [
            { field: 'namespace', headerName: 'namespace', flex: 1, sortable: true },
            { field: 'service', headerName: 'service name', flex: 1.2, sortable: true },
            { field: 'hostCount', headerName: 'hosts', flex: 0.7, sortable: true },
            { field: 'podCount', headerName: 'pods', flex: 0.7, sortable: true },
            { field: 'containerCount', headerName: 'containers', flex: 0.8, sortable: true },
            { field: 'processCount', headerName: 'processes', flex: 0.8, sortable: true },
            ...sharedStatusColumns,
            sharedTimestampColumn,
        ];
    }

    if (scope === 'host') {
        return [
            { field: 'service', headerName: 'service name', flex: 1, sortable: true },
            { field: 'namespace', headerName: 'namespace', flex: 0.9, sortable: true },
            { field: 'host', headerName: 'host name', flex: 1.1, sortable: true },
            { field: 'pids', headerName: 'pids (if profiled)', flex: 1, sortable: false },
            { field: 'ip', headerName: 'IP', flex: 0.9, sortable: true },
            { field: 'commandType', headerName: 'command type', flex: 0.8, sortable: true },
            { field: 'profilingStatus', headerName: 'profiling status', flex: 0.9, sortable: true },
            sharedTimestampColumn,
            {
                field: 'profile',
                headerName: 'profile',
                flex: 1.2,
                sortable: false,
                renderCell: (params) => {
                    const { host, service, commandType, profilingStatus } = params.row;
                    if (commandType !== 'start' || profilingStatus !== 'active' || !host || !service) {
                        return '';
                    }

                    return (
                        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
                            <a href={buildProfileUrl(host, service, 'flamegraph')} target="_blank" rel="noopener noreferrer">
                                View Continuous Profile
                            </a>
                            <a href={buildProfileUrl(host, service, 'adhoc')} target="_blank" rel="noopener noreferrer">
                                View Adhoc Profile
                            </a>
                        </Box>
                    );
                },
            },
        ];
    }

    if (scope === 'pod') {
        return [
            { field: 'podName', headerName: 'pod', flex: 1.2, sortable: true },
            { field: 'namespace', headerName: 'namespace', flex: 0.9, sortable: true },
            { field: 'service', headerName: 'service name', flex: 1, sortable: true },
            { field: 'hostCount', headerName: 'hosts', flex: 0.7, sortable: true },
            { field: 'containerCount', headerName: 'containers', flex: 0.8, sortable: true },
            { field: 'processCount', headerName: 'processes', flex: 0.8, sortable: true },
            ...sharedStatusColumns,
            sharedTimestampColumn,
        ];
    }

    if (scope === 'container') {
        return [
            { field: 'containerName', headerName: 'container', flex: 1.1, sortable: true },
            { field: 'podName', headerName: 'pod', flex: 1, sortable: true },
            { field: 'namespace', headerName: 'namespace', flex: 0.9, sortable: true },
            { field: 'host', headerName: 'host', flex: 1, sortable: true },
            { field: 'processCount', headerName: 'processes', flex: 0.8, sortable: true },
            ...sharedStatusColumns,
            sharedTimestampColumn,
        ];
    }

    return [
        { field: 'processName', headerName: 'process', flex: 1.1, sortable: true },
        { field: 'pid', headerName: 'pid', flex: 0.6, sortable: true },
        { field: 'containerName', headerName: 'container', flex: 1, sortable: true },
        { field: 'podName', headerName: 'pod', flex: 1, sortable: true },
        { field: 'namespace', headerName: 'namespace', flex: 0.9, sortable: true },
        { field: 'host', headerName: 'host', flex: 1, sortable: true },
        ...sharedStatusColumns,
        sharedTimestampColumn,
    ];
};

const formatRowForScope = (row, scope) => ({
    id: readField(row, 'id'),
    scope,
    service: readField(row, 'serviceName', 'service_name'),
    namespace: readField(row, 'namespace'),
    host: readField(row, 'hostname'),
    ip: readField(row, 'ipAddress', 'ip_address'),
    podName: readField(row, 'podName', 'pod_name'),
    containerName: readField(row, 'containerName', 'container_name'),
    workloadName: readField(row, 'workloadName', 'workload_name'),
    workloadKind: readField(row, 'workloadKind', 'workload_kind'),
    processName: readField(row, 'processName', 'process_name'),
    pid: readField(row, 'pid'),
    pids: readField(row, 'pids', 'pids') || [],
    activeHosts: readField(row, 'activeHosts', 'active_hosts'),
    hostCount: readField(row, 'hostCount', 'host_count'),
    namespaceCount: readField(row, 'namespaceCount', 'namespace_count'),
    podCount: readField(row, 'podCount', 'pod_count'),
    containerCount: readField(row, 'containerCount', 'container_count'),
    processCount: readField(row, 'processCount', 'process_count'),
    commandType: readField(row, 'commandType', 'command_type') || 'N/A',
    profilingStatus: readField(row, 'profilingStatus', 'profiling_status') || 'stopped',
    profilingMode: readField(row, 'profilingMode', 'profiling_mode') || 'N/A',
    frequency: readField(row, 'frequency'),
    profilerSummary: readField(row, 'profilerSummary', 'profiler_summary') || 'N/A',
    heartbeatTimestamp: readField(row, 'heartbeatTimestamp', 'heartbeat_timestamp'),
    agentVersion: readField(row, 'agentVersion', 'agent_version'),
    runMode: readField(row, 'runMode', 'run_mode'),
});

const scopeEntityLabel = (scope) => {
    const lookup = {
        service: 'services',
        namespace: 'namespaces',
        host: 'hosts',
        pod: 'pods',
        container: 'containers',
        process: 'processes',
    };
    return lookup[scope] || 'entities';
};

const rowDisplayName = (row, scope) => {
    if (scope === 'service') return row.service;
    if (scope === 'namespace') return `${row.namespace}`;
    if (scope === 'host') return row.host;
    if (scope === 'pod') return `${row.namespace}/${row.podName}`;
    if (scope === 'container') return `${row.namespace}/${row.podName}/${row.containerName}`;
    return `${row.processName} (${row.pid})`;
};

const buildTargetEntity = (row) => ({
    id: row.id,
    service_name: row.service,
    namespace: row.namespace || undefined,
    hostname: row.host || undefined,
    ip_address: row.ip || undefined,
    pod_name: row.podName || undefined,
    container_name: row.containerName || undefined,
    workload_name: row.workloadName || undefined,
    workload_kind: row.workloadKind || undefined,
    pid: row.pid || undefined,
    process_name: row.processName || undefined,
});

const ProfilingStatusPage = () => {
    const history = useHistory();
    const location = useLocation();

    const [activeScope, setActiveScope] = useState('service');
    const [rows, setRows] = useState([]);
    const [scopeCounts, setScopeCounts] = useState({});
    const [loading, setLoading] = useState(false);
    const [selectionModel, setSelectionModel] = useState([]);
    const [activeCount, setActiveCount] = useState(0);
    const [totalCount, setTotalCount] = useState(0);
    const [filters, setFilters] = useState(EMPTY_FILTERS);
    const [appliedFilters, setAppliedFilters] = useState(EMPTY_FILTERS);
    const [enablePerfSpect, setEnablePerfSpect] = useState(false);
    const [profilingFrequency, setProfilingFrequency] = useState(DEFAULT_PROFILING_FREQUENCY);
    const [maxProcesses, setMaxProcesses] = useState(DEFAULT_MAX_PROCESSES);
    const [profilingMode, setProfilingMode] = useState('continuous');
    const [duration, setDuration] = useState(DEFAULT_DURATION);
    const [profilerConfigs, setProfilerConfigs] = useState({
        perf: { mode: 'enabled_restricted', events: ['cpu-cycles'] },
        async_profiler: { enabled: true, time: 'cpu' },
        pyperf: 'enabled',
        pyspy: 'enabled_fallback',
        rbspy: 'disabled',
        phpspy: 'disabled',
        dotnet_trace: 'disabled',
        nodejs_perf: 'enabled',
    });
    const [confirmationDialog, setConfirmationDialog] = useState({
        open: false,
        action: null,
        selectedRows: [],
        serviceGroups: {},
    });
    const [dryRunValidation, setDryRunValidation] = useState({
        isValidating: false,
        isValid: false,
        errors: [],
    });
    const [snackbar, setSnackbar] = useState({ open: false, message: '' });

    const columns = useMemo(() => getScopeColumns(activeScope), [activeScope]);

    useEffect(() => {
        try {
            const saved = JSON.parse(localStorage.getItem(CONFIG_STORAGE_KEY));
            if (saved) {
                if (typeof saved.enablePerfSpect === 'boolean') setEnablePerfSpect(saved.enablePerfSpect);
                if (saved.profilingFrequency) setProfilingFrequency(saved.profilingFrequency);
                if (saved.maxProcesses != null) setMaxProcesses(saved.maxProcesses);
                if (saved.profilingMode) setProfilingMode(saved.profilingMode);
                if (saved.duration) setDuration(saved.duration);
                if (saved.profilerConfigs) setProfilerConfigs(saved.profilerConfigs);
            }
        } catch (error) {
            // ignore malformed persisted config
        }
    }, []);

    const handleSaveConfiguration = useCallback(() => {
        const config = {
            enablePerfSpect,
            profilingFrequency,
            maxProcesses,
            profilingMode,
            duration,
            profilerConfigs,
        };
        try {
            localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(config));
            setSnackbar({ open: true, message: 'Configuration saved' });
        } catch (error) {
            setSnackbar({ open: true, message: 'Failed to save configuration' });
        }
    }, [duration, enablePerfSpect, maxProcesses, profilerConfigs, profilingFrequency, profilingMode]);

    const fetchProfilingStatus = useCallback((filterParams, scope = activeScope) => {
        setLoading(true);
        const params = new URLSearchParams();
        params.append('scope', scope);

        if (filterParams.service) params.append('service_name', filterParams.service);
        if (filterParams.hostname) params.append('hostname', filterParams.hostname);
        if (filterParams.pids) params.append('pids', filterParams.pids);
        if (filterParams.ip) params.append('ip_address', filterParams.ip);
        if (filterParams.namespace) params.append('namespace', filterParams.namespace);
        if (filterParams.podName) params.append('pod_name', filterParams.podName);
        if (filterParams.containerName) params.append('container_name', filterParams.containerName);
        if (filterParams.processName) params.append('process_name', filterParams.processName);
        if (filterParams.commandType) params.append('command_type', filterParams.commandType);
        if (filterParams.status) params.append('profiling_status', filterParams.status);

        fetch(`${DATA_URLS.GET_PROFILING_WORKLOAD_STATUS}?${params.toString()}`)
            .then((res) => res.json())
            .then((data) => {
                const normalizedRows = (data.rows || []).map((row) => formatRowForScope(row, scope));
                setRows(normalizedRows);
                setScopeCounts(data.tabCounts || data.tab_counts || {});
                setActiveCount(data.activeHosts || data.active_hosts || 0);
                setTotalCount(data.totalCount || data.total_count || normalizedRows.length);
                setLoading(false);
            })
            .catch(() => setLoading(false));
    }, [activeScope]);

    const updateURL = useCallback((scope, nextFilters) => {
        const searchParams = { scope };
        Object.keys(nextFilters).forEach((key) => {
            if (nextFilters[key]) {
                searchParams[key] = nextFilters[key];
            }
        });
        history.replace({ pathname: '/profiling', search: queryString.stringify(searchParams) });
    }, [history]);

    const applyFilters = useCallback(() => {
        setAppliedFilters(filters);
        setSelectionModel([]);
        fetchProfilingStatus(filters, activeScope);
        updateURL(activeScope, filters);
    }, [activeScope, fetchProfilingStatus, filters, updateURL]);

    const clearAllFilters = useCallback(() => {
        setFilters(EMPTY_FILTERS);
        setAppliedFilters(EMPTY_FILTERS);
        setSelectionModel([]);
        fetchProfilingStatus(EMPTY_FILTERS, activeScope);
        updateURL(activeScope, EMPTY_FILTERS);
    }, [activeScope, fetchProfilingStatus, updateURL]);

    const updateFilter = useCallback((field, value) => {
        setFilters((prev) => ({ ...prev, [field]: value }));
    }, []);

    useEffect(() => {
        const searchParams = queryString.parse(location.search);
        const scope = searchParams.scope || 'service';
        const urlFilters = {
            service: searchParams.service || '',
            hostname: searchParams.hostname || '',
            pids: searchParams.pids || '',
            ip: searchParams.ip || '',
            namespace: searchParams.namespace || '',
            podName: searchParams.podName || '',
            containerName: searchParams.containerName || '',
            processName: searchParams.processName || '',
            commandType: searchParams.commandType || '',
            status: searchParams.status || '',
        };
        setActiveScope(scope);
        setFilters(urlFilters);
        setAppliedFilters(urlFilters);
        fetchProfilingStatus(urlFilters, scope);
    }, [fetchProfilingStatus, location.search]);

    useEffect(() => {
        const refreshInterval = setInterval(() => {
            fetchProfilingStatus(appliedFilters, activeScope);
        }, 30000);
        return () => clearInterval(refreshInterval);
    }, [activeScope, appliedFilters, fetchProfilingStatus]);

    const handleScopeChange = (_, nextScope) => {
        setActiveScope(nextScope);
        setSelectionModel([]);
        fetchProfilingStatus(appliedFilters, nextScope);
        updateURL(nextScope, appliedFilters);
    };

    const buildRequests = useCallback((action, selectedRows) => {
        const grouped = selectedRows.reduce((groups, row) => {
            if (!groups[row.service]) {
                groups[row.service] = [];
            }
            groups[row.service].push(row);
            return groups;
        }, {});

        const requests = Object.entries(grouped).map(([serviceName, serviceRows]) => {
            const targetScope = activeScope;
            const targetHosts = targetScope === 'host'
                ? serviceRows.reduce((hostMap, row) => {
                    hostMap[row.host] = null;
                    return hostMap;
                }, {})
                : undefined;

            return {
                service_name: serviceName,
                request_type: action,
                continuous: profilingMode === 'continuous',
                duration: profilingMode === 'continuous' ? 60 : duration,
                frequency: profilingFrequency,
                profiling_mode: 'cpu',
                target_scope: targetScope,
                target_hosts: targetHosts,
                target_entities: serviceRows.map((row) => buildTargetEntity(row)),
                stop_level: action === 'stop' ? (['service', 'host'].includes(targetScope) ? 'host' : 'process') : undefined,
                additional_args: {
                    enable_perfspect: enablePerfSpect,
                    profiler_configs: profilerConfigs,
                    max_processes: maxProcesses,
                },
            };
        });

        return { grouped, requests };
    }, [activeScope, duration, enablePerfSpect, maxProcesses, profilerConfigs, profilingFrequency, profilingMode]);

    const executeDryRun = useCallback((action, selectedRows) => {
        const { requests } = buildRequests(action, selectedRows);
        setDryRunValidation({ isValidating: true, isValid: false, errors: [] });

        fetch(DATA_URLS.POST_PROFILING_REQUEST_BULK, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ requests, dry_run: true }),
        })
            .then((res) => {
                if (!res.ok) {
                    return res.json().then((errData) => {
                        if (errData.detail && Array.isArray(errData.detail)) {
                            throw new Error(errData.detail.map((err) => err.msg || JSON.stringify(err)).join('; '));
                        }
                        throw new Error(errData.detail || `Validation failed with status ${res.status}`);
                    });
                }
                return res.json();
            })
            .then((bulkResponse) => {
                const errors = (bulkResponse.results || [])
                    .filter((result) => !result.success)
                    .map((result) => `${result.service_name}: ${result.error}`);
                setDryRunValidation({
                    isValidating: false,
                    isValid: (bulkResponse.failed_count || 0) === 0,
                    errors,
                });
            })
            .catch((error) => {
                setDryRunValidation({
                    isValidating: false,
                    isValid: false,
                    errors: [error.message],
                });
            });
    }, [buildRequests]);

    const handleBulkAction = (action) => {
        const selectedRows = rows.filter((row) => selectionModel.includes(row.id));
        const { grouped } = buildRequests(action, selectedRows);
        setConfirmationDialog({
            open: true,
            action,
            selectedRows,
            serviceGroups: grouped,
        });
        executeDryRun(action, selectedRows);
    };

    const executeProfilingAction = () => {
        const { action, selectedRows } = confirmationDialog;
        const { requests } = buildRequests(action, selectedRows);

        setConfirmationDialog({ open: false, action: null, selectedRows: [], serviceGroups: {} });
        setDryRunValidation({ isValidating: false, isValid: false, errors: [] });

        fetch(DATA_URLS.POST_PROFILING_REQUEST_BULK, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ requests, dry_run: false }),
        }).then(() => {
            fetchProfilingStatus(appliedFilters, activeScope);
            setSelectionModel([]);
            setEnablePerfSpect(false);
        });
    };

    const handleDialogClose = () => {
        setConfirmationDialog({ open: false, action: null, selectedRows: [], serviceGroups: {} });
        setDryRunValidation({ isValidating: false, isValid: false, errors: [] });
    };

    return (
        <Box sx={{ backgroundColor: 'white.main', height: '100%' }}>
            <Box
                sx={{
                    px: 4,
                    pt: 3,
                    pb: 1,
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'flex-start',
                    gap: 2,
                }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                    <Icon name={ICONS_NAMES.Crosshairs} size={28} color="#583FFD" />
                    <Box>
                        <Typography variant="h5" sx={{ fontWeight: 700, lineHeight: 1.2 }}>
                            Adhoc Profile Configuration
                        </Typography>
                        <Typography variant="body2" color="text.secondary">
                            Select workloads and configure profiling parameters
                        </Typography>
                    </Box>
                </Box>
                <Box sx={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                        Active Hosts
                    </Typography>
                    <Typography variant="subtitle1" sx={{ fontWeight: 700, color: '#16a34a' }}>
                        {(activeCount || 0).toLocaleString()} of {(totalCount || 0).toLocaleString()}
                    </Typography>
                </Box>
            </Box>

            <ProfilingHeader
                filters={filters}
                updateFilter={updateFilter}
                isLoading={loading}
                onApplyFilters={applyFilters}
                onClearFilters={clearAllFilters}
            />

            <Box sx={{ px: 4, pt: 2 }}>
                <Tabs value={activeScope} onChange={handleScopeChange} variant="scrollable" scrollButtons="auto">
                    {SCOPES.map((scope) => (
                        <Tab
                            key={scope.id}
                            value={scope.id}
                            label={`${scope.label} ${scopeCounts[scope.id] ? scopeCounts[scope.id] : ''}`.trim()}
                        />
                    ))}
                </Tabs>
            </Box>

            <Box sx={{ display: 'flex', flexDirection: 'column' }}>
                <Box sx={{ px: 4, pt: 3, pb: 1, '& .MuiDataGrid-root': { border: 'none' } }}>
                    <MuiTable
                        columns={columns}
                        data={rows}
                        isLoading={loading}
                        pageSize={50}
                        rowHeight={50}
                        autoPageSize
                        checkboxSelection
                        onSelectionModelChange={setSelectionModel}
                        selectionModel={selectionModel}
                    />
                    <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                        Showing {rows.length} {scopeEntityLabel(activeScope)}
                        {selectionModel.length ? ` \u00b7 ${selectionModel.length} selected` : ''}
                    </Typography>
                </Box>

                <ProfilingTopPanel
                    selectionModel={selectionModel}
                    handleBulkAction={handleBulkAction}
                    fetchProfilingStatus={(scopeFilters) => fetchProfilingStatus(scopeFilters, activeScope)}
                    filters={appliedFilters}
                    loading={loading}
                    enablePerfSpect={enablePerfSpect}
                    onPerfSpectChange={setEnablePerfSpect}
                    profilingFrequency={profilingFrequency}
                    onProfilingFrequencyChange={setProfilingFrequency}
                    maxProcesses={maxProcesses}
                    onMaxProcessesChange={setMaxProcesses}
                    profilingMode={profilingMode}
                    onProfilingModeChange={setProfilingMode}
                    duration={duration}
                    onDurationChange={setDuration}
                    profilerConfigs={profilerConfigs}
                    onProfilerConfigsChange={setProfilerConfigs}
                    onSaveConfiguration={handleSaveConfiguration}
                />
            </Box>

            <Dialog open={confirmationDialog.open} onClose={handleDialogClose} maxWidth="md" fullWidth>
                <DialogTitle>
                    <Typography variant="h6">
                        Confirm {confirmationDialog.action === 'start' ? 'Start' : 'Stop'} Profiling
                    </Typography>
                </DialogTitle>
                <DialogContent>
                    {dryRunValidation.isValidating && (
                        <Box sx={{ mb: 2, p: 2, backgroundColor: '#f5f5f5', borderRadius: 1 }}>
                            <Typography variant="body2" color="text.secondary">
                                Validating request...
                            </Typography>
                        </Box>
                    )}
                    {!dryRunValidation.isValidating && dryRunValidation.isValid && (
                        <Box sx={{ mb: 2, p: 2, backgroundColor: '#e8f5e9', borderRadius: 1 }}>
                            <Typography variant="body2" color="success.dark">
                                ✓ Validation successful
                            </Typography>
                        </Box>
                    )}
                    {!dryRunValidation.isValidating && !dryRunValidation.isValid && dryRunValidation.errors.length > 0 && (
                        <Box sx={{ mb: 2, p: 2, backgroundColor: '#ffebee', borderRadius: 1 }}>
                            <Typography variant="body2" color="error.dark" sx={{ fontWeight: 600, mb: 1 }}>
                                ✗ Validation failed
                            </Typography>
                            {dryRunValidation.errors.map((error, idx) => (
                                <Typography key={idx} variant="body2" color="error.dark" sx={{ mt: 0.5, whiteSpace: 'pre-line' }}>
                                    • {error}
                                </Typography>
                            ))}
                        </Box>
                    )}

                    <Typography variant="body1" sx={{ mb: 2 }}>
                        Are you sure you want to <strong>{confirmationDialog.action}</strong> profiling for the selected {scopeEntityLabel(activeScope)}?
                    </Typography>

                    <Box sx={{ mb: 3 }}>
                        <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
                            Selected {scopeEntityLabel(activeScope)} ({confirmationDialog.selectedRows.length}):
                        </Typography>
                        {Object.entries(confirmationDialog.serviceGroups).map(([serviceName, serviceRows]) => (
                            <Box key={serviceName} sx={{ mb: 2 }}>
                                <Typography variant="body2" sx={{ fontWeight: 500, mb: 0.5 }}>
                                    {serviceName}:
                                </Typography>
                                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                                    {serviceRows.map((row) => (
                                        <Chip
                                            key={row.id}
                                            label={rowDisplayName(row, activeScope)}
                                            size="small"
                                            variant="outlined"
                                            sx={{
                                                borderColor: dryRunValidation.isValid ? '#2e7d32' : '#d32f2f',
                                                color: dryRunValidation.isValid ? '#2e7d32' : '#d32f2f',
                                            }}
                                        />
                                    ))}
                                </Box>
                            </Box>
                        ))}
                    </Box>

                    {confirmationDialog.action === 'start' && (
                        <>
                            <Divider sx={{ my: 2 }} />
                            <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 600 }}>
                                Profiling Configuration:
                            </Typography>
                            <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 2 }}>
                                <Box>
                                    <Typography variant="body2" sx={{ fontWeight: 500, mb: 1 }}>
                                        Basic Settings:
                                    </Typography>
                                    <Typography variant="body2">• Frequency: {profilingFrequency} Hz</Typography>
                                    <Typography variant="body2">• Max Processes: {maxProcesses}</Typography>
                                    <Typography variant="body2">• PerfSpect HW Metrics: {enablePerfSpect ? 'Enabled' : 'Disabled'}</Typography>
                                    <Typography variant="body2">• Profiling Mode: {profilingMode === 'adhoc' ? 'Ad Hoc' : 'Continuous'}</Typography>
                                    <Typography variant="body2">• Duration: {profilingMode === 'continuous' ? 60 : duration} seconds</Typography>
                                    <Typography variant="body2">• Mode: CPU profiling</Typography>
                                </Box>
                                <Box>
                                    <Typography variant="body2" sx={{ fontWeight: 500, mb: 1 }}>
                                        Profiler Settings:
                                    </Typography>
                                    <Typography variant="body2">• Perf (C/C++/Go): {
                                        profilerConfigs.perf?.mode === 'enabled_restricted' ? 'Enabled (Restricted)' :
                                        profilerConfigs.perf?.mode === 'enabled_aggressive' ? 'Enabled (Aggressive)' : 'Disabled'
                                    }</Typography>
                                    <Typography variant="body2">• Java Async Profiler: {
                                        profilerConfigs.async_profiler?.enabled
                                            ? `Enabled (${profilerConfigs.async_profiler.time === 'wall' ? 'Wall Time' : 'CPU Time'})`
                                            : 'Disabled'
                                    }</Typography>
                                    <Typography variant="body2">• Pyperf (Python): {profilerConfigs.pyperf === 'enabled' ? 'Enabled' : 'Disabled'}</Typography>
                                    <Typography variant="body2">• Pyspy (Python): {
                                        profilerConfigs.pyspy === 'enabled_fallback' ? 'Enabled (Fallback)' :
                                        profilerConfigs.pyspy === 'enabled' ? 'Enabled' : 'Disabled'
                                    }</Typography>
                                    <Typography variant="body2">• Rbspy (Ruby): {profilerConfigs.rbspy === 'enabled' ? 'Enabled' : 'Disabled'}</Typography>
                                    <Typography variant="body2">• PHPspy (PHP): {profilerConfigs.phpspy === 'enabled' ? 'Enabled' : 'Disabled'}</Typography>
                                    <Typography variant="body2">• .NET Trace: {profilerConfigs.dotnet_trace === 'enabled' ? 'Enabled' : 'Disabled'}</Typography>
                                    <Typography variant="body2">• NodeJS Perf: {profilerConfigs.nodejs_perf === 'enabled' ? 'Enabled' : 'Disabled'}</Typography>
                                </Box>
                            </Box>
                        </>
                    )}
                </DialogContent>
                <DialogActions>
                    <Button onClick={handleDialogClose}>Cancel</Button>
                    <Button
                        onClick={executeProfilingAction}
                        color={confirmationDialog.action === 'start' ? 'success' : 'error'}
                        variant="contained"
                        disabled={dryRunValidation.isValidating || !dryRunValidation.isValid}
                    >
                        {confirmationDialog.action === 'start' ? 'Start Profiling' : 'Stop Profiling'}
                    </Button>
                </DialogActions>
            </Dialog>

            <Snackbar
                open={snackbar.open}
                autoHideDuration={3000}
                onClose={() => setSnackbar({ open: false, message: '' })}
                anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
            >
                <Alert
                    onClose={() => setSnackbar({ open: false, message: '' })}
                    severity="success"
                    variant="filled"
                    sx={{ width: '100%' }}
                >
                    {snackbar.message}
                </Alert>
            </Snackbar>
        </Box>
    );
};

export default ProfilingStatusPage;
