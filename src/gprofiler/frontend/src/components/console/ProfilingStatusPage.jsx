import { Box, Button, duration, Typography } from '@mui/material';
import TextField from '@mui/material/TextField';
import queryString from 'query-string';
import React, { useCallback, useEffect, useState } from 'react';
import { useHistory, useLocation } from 'react-router-dom';

import { formatDate, TIME_FORMATS } from '../../utils/datetimesUtils';
import MuiTable from '../common/dataDisplay/table/MuiTable';
import PageHeader from '../common/layout/PageHeader';

const columns = [
    { field: 'service', headerName: 'service name', flex: 1 },
    { field: 'host', headerName: 'host name', flex: 1 },
    { field: 'pids', headerName: 'pids (if profiled)', flex: 1 },
    { field: 'ip', headerName: 'IP', flex: 1 },
    { field: 'commandType', headerName: 'command type', flex: 1 },
    { field: 'status', headerName: 'profiling status', flex: 1 },
    {
        field: 'heartbeat_timestamp',
        headerName: 'last heartbeat',
        flex: 1,
        renderCell: (params) => {
            if (!params.value) return 'N/A';
            try {
                // The backend sends UTC timestamp without 'Z' suffix, so we need to explicitly treat it as UTC
                let utcTimestamp = params.value;
                if (!utcTimestamp.endsWith('Z') && !utcTimestamp.includes('+') && !utcTimestamp.includes('-', 10)) {
                    utcTimestamp += 'Z';
                }

                const utcDate = new Date(utcTimestamp);
                // Convert to user's local timezone
                const localDateTimeString = utcDate.toLocaleString(navigator.language, {
                    day: '2-digit',
                    month: '2-digit',
                    year: 'numeric',
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit',
                    hour12: true,
                });
                return localDateTimeString;
            } catch (error) {
                return 'Invalid date';
            }
        },
    },
];

const ProfilingStatusPage = () => {
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(false);
    const [selectionModel, setSelectionModel] = useState([]);
    const [filter, setFilter] = useState('');
    const history = useHistory();
    const location = useLocation();

    // Initialize filter from URL parameters on component mount
    useEffect(() => {
        const searchParams = queryString.parse(location.search);
        const serviceParam = searchParams.service;
        if (serviceParam) {
            setFilter(serviceParam);
        }
    }, [location.search]);

    // Update URL when filter changes
    const updateURL = useCallback(
        (serviceName) => {
            const searchParams = queryString.parse(location.search);
            if (serviceName && serviceName.length >= 3) {
                searchParams.service = serviceName;
            } else {
                delete searchParams.service;
            }
            history.push({ search: queryString.stringify(searchParams) });
        },
        [location.search, history]
    );

    const fetchProfilingStatus = useCallback((serviceName = null) => {
        setLoading(true);
        const url = serviceName
            ? `/api/metrics/profiling/host_status?service_name=${encodeURIComponent(serviceName)}`
            : '/api/metrics/profiling/host_status';

        fetch(url)
            .then((res) => res.json())
            .then((data) => {
                setRows(
                    data.map((row) => ({
                        id: row.id,
                        service: row.service_name,
                        host: row.hostname,
                        pids: row.pids,
                        ip: row.ip_address,
                        commandType: row.command_type || 'N/A',
                        status: row.profiling_status,
                        heartbeat_timestamp: row.heartbeat_timestamp,
                    }))
                );
                setLoading(false);
            })
            .catch(() => setLoading(false));
    }, []);

    // Initial data fetch - check URL first, then fetch data
    useEffect(() => {
        const searchParams = queryString.parse(location.search);
        const serviceParam = searchParams.service;
        if (serviceParam) {
            // If there's a service parameter in URL, fetch with that filter
            fetchProfilingStatus(serviceParam);
        } else {
            // Otherwise fetch all data
            fetchProfilingStatus();
        }
    }, []); // Only run once on mount

    // Debounced function to handle filter changes
    useEffect(() => {
        const timeoutId = setTimeout(() => {
            if (filter.length >= 3) {
                // Call backend with service name filter
                fetchProfilingStatus(filter);
                updateURL(filter);
            } else if (filter.length === 0) {
                // Get all hosts when filter is empty
                fetchProfilingStatus();
                updateURL('');
            }
            // Do nothing if filter is 1-2 characters (show existing data)
        }, 500); // 500ms debounce

        return () => clearTimeout(timeoutId);
    }, [filter, fetchProfilingStatus, updateURL]);

    // Bulk Start/Stop handlers
    function handleBulkAction(action) {
        const selectedRows = rows.filter((row) => selectionModel.includes(row.id));

        // Group selected rows by service name
        const serviceGroups = selectedRows.reduce((groups, row) => {
            if (!groups[row.service]) {
                groups[row.service] = [];
            }
            groups[row.service].push(row.host);
            return groups;
        }, {});

        // Create one request per service with all hosts for that service
        const requests = Object.entries(serviceGroups).map(([serviceName, hosts]) => {
            const target_host = hosts.reduce((hostObj, host) => {
                hostObj[host] = null;
                return hostObj;
            }, {});

            const submitData = {
                service_name: serviceName,
                request_type: action,
                continuous: true,
                duration: 60, // Default duration, can't be adjusted yet
                frequency: 11, // Default frequency, can't be adjusted yet
                profiling_mode: 'cpu', // Default profiling mode, can't be adjusted yet
                target_hosts: target_host,
            };

            // append 'stop_level: host' when action is 'stop'
            if (action === 'stop') {
                submitData.stop_level = 'host';
            }

            return fetch('/api/metrics/profile_request', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(submitData),
            });
        });

        // Wait for all requests to finish before refreshing
        Promise.all(requests).then(() => {
            // Maintain current filter state when refreshing
            if (filter.length >= 3) {
                fetchProfilingStatus(filter);
            } else {
                fetchProfilingStatus();
            }
            setSelectionModel([]); // Clear all checkboxes after API requests complete
        });
    }

    return (
        <>
            <PageHeader title='Dynamic Profiling' />
            <Box sx={{ p: 3 }}>
                <Box sx={{ mb: 2, display: 'flex', gap: 2, alignItems: 'center' }}>
                    <TextField
                        label='Filter by service name'
                        variant='outlined'
                        size='small'
                        value={filter}
                        onChange={(e) => setFilter(e.target.value)}
                        sx={{ minWidth: 250 }}
                        helperText={filter.length > 0 && filter.length < 3 ? 'Type 3+ characters to filter' : ''}
                    />
                    <Button
                        variant='contained'
                        color='success'
                        size='small'
                        onClick={() => handleBulkAction('start')}
                        disabled={selectionModel.length === 0}>
                        Start
                    </Button>
                    <Button
                        variant='contained'
                        color='error'
                        size='small'
                        onClick={() => handleBulkAction('stop')}
                        disabled={selectionModel.length === 0}>
                        Stop
                    </Button>
                    <Button
                        variant='outlined'
                        color='primary'
                        size='small'
                        onClick={() => {
                            if (filter.length >= 3) {
                                fetchProfilingStatus(filter);
                            } else {
                                fetchProfilingStatus();
                            }
                        }}
                        disabled={loading}>
                        Refresh
                    </Button>
                </Box>
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
            </Box>
        </>
    );
};

export default ProfilingStatusPage;
