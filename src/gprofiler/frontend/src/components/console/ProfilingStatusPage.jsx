import { Box, Button, Collapse, FormControl, InputLabel, MenuItem, Select, TextField, Typography } from '@mui/material';
import queryString from 'query-string';
import React, { useCallback, useEffect, useState } from 'react';
import { useHistory, useLocation } from 'react-router-dom';

import { DATA_URLS } from '../../api/urls';
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
    const [filtersOpen, setFiltersOpen] = useState(false);
    const [filters, setFilters] = useState({
        service: '',
        hostname: '',
        pids: '',
        ip: '',
        commandType: '',
        status: '',
    });
    const history = useHistory();
    const location = useLocation();

    // Initialize filters from URL parameters on component mount
    useEffect(() => {
        const searchParams = queryString.parse(location.search);
        const newFilters = {
            service: searchParams.service || '',
            hostname: searchParams.hostname || '',
            pids: searchParams.pids || '',
            ip: searchParams.ip || '',
            commandType: searchParams.commandType || '',
            status: searchParams.status || '',
        };
        setFilters(newFilters);

        // Open filters panel if any filters are active
        const hasActiveFilters = Object.values(newFilters).some((value) => value);
        setFiltersOpen(hasActiveFilters);
    }, [location.search]);

    // Clean up profile-specific URL parameters when entering dynamic profiling
    useEffect(() => {
        const searchParams = queryString.parse(location.search);
        let hasProfileParams = false;

        // Check if any profile-specific parameters exist
        const profileParams = [
            'gtab',
            'view',
            'time',
            'startTime',
            'endTime',
            'filter',
            'rt',
            'rtms',
            'p',
            'pm',
            'wt',
            'wp',
            'search',
            'fullscreen',
        ];

        profileParams.forEach((param) => {
            if (searchParams[param] !== undefined) {
                delete searchParams[param];
                hasProfileParams = true;
            }
        });

        // Only update URL if we found and removed profile-specific parameters
        if (hasProfileParams) {
            history.replace({ search: queryString.stringify(searchParams) });
        }
    }, []); // Run only once on component mount

    // Update URL when filters change
    const updateURL = useCallback(
        (newFilters) => {
            const searchParams = queryString.parse(location.search);

            // Update all filter parameters
            Object.keys(newFilters).forEach((key) => {
                if (newFilters[key]) {
                    searchParams[key] = newFilters[key];
                } else {
                    delete searchParams[key];
                }
            });

            history.push({ search: queryString.stringify(searchParams) });
        },
        [location.search, history]
    );

    const fetchProfilingStatus = useCallback(
        (filterParams = filters) => {
            setLoading(true);

            // Build query parameters
            const params = new URLSearchParams();

            if (filterParams.service) {
                params.append('service_name', filterParams.service);
            }
            if (filterParams.hostname) {
                params.append('hostname', filterParams.hostname);
            }
            if (filterParams.pids) {
                params.append('pids', filterParams.pids);
            }
            if (filterParams.ip) {
                params.append('ip_address', filterParams.ip);
            }
            if (filterParams.commandType) {
                params.append('command_type', filterParams.commandType);
            }
            if (filterParams.status) {
                params.append('profiling_status', filterParams.status);
            }

            const url = params.toString()
                ? `${DATA_URLS.GET_PROFILING_HOST_STATUS}?${params.toString()}`
                : DATA_URLS.GET_PROFILING_HOST_STATUS;

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
        },
        [filters]
    );

    // Initial data fetch
    useEffect(() => {
        fetchProfilingStatus(filters);
    }, []); // Only run once on mount

    // Debounced function to handle filter changes
    useEffect(() => {
        const timeoutId = setTimeout(() => {
            fetchProfilingStatus(filters);
            updateURL(filters);
        }, 300); // 300ms debounce

        return () => clearTimeout(timeoutId);
    }, [filters, fetchProfilingStatus, updateURL]);

    // Function to update individual filter
    const updateFilter = (field, value) => {
        const newFilters = { ...filters, [field]: value };
        setFilters(newFilters);
    };

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

            return fetch(DATA_URLS.POST_PROFILING_REQUEST, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(submitData),
            });
        });

        // Wait for all requests to finish before refreshing
        Promise.all(requests).then(() => {
            // Maintain current filter state when refreshing
            fetchProfilingStatus(filters);
            setSelectionModel([]); // Clear all checkboxes after API requests complete
        });
    }

    return (
        <>
            <PageHeader title='Dynamic Profiling' />
            <Box sx={{ p: { xs: 2, sm: 3, md: 4 }, backgroundColor: 'white.main', minHeight: 'calc(100vh - 100px)' }}>
                {/* Filter Panel */}
                <Box sx={{ mb: 3 }}>
                    <Button variant='outlined' onClick={() => setFiltersOpen(!filtersOpen)} sx={{ mb: 2 }}>
                        🔍 Filters {filtersOpen ? '▲' : '▼'}{' '}
                        {Object.values(filters).filter((value) => value).length > 0 &&
                            `(${Object.values(filters).filter((value) => value).length} active)`}
                    </Button>

                    <Collapse in={filtersOpen} timeout='auto'>
                        <Box
                            sx={{
                                p: 3,
                                border: '1px solid #e0e0e0',
                                borderRadius: 1,
                                backgroundColor: '#fafafa',
                                display: 'grid',
                                gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))',
                                gap: 2,
                                mb: 2,
                            }}>
                            {/* Service Name Filter */}
                            <TextField
                                label='Service Name'
                                variant='outlined'
                                size='small'
                                value={filters.service}
                                onChange={(e) => updateFilter('service', e.target.value)}
                                placeholder='Filter by service name...'
                            />

                            {/* Hostname Filter */}
                            <TextField
                                label='Hostname'
                                variant='outlined'
                                size='small'
                                value={filters.hostname}
                                onChange={(e) => updateFilter('hostname', e.target.value)}
                                placeholder='Filter by hostname...'
                            />

                            {/* PIDs Filter */}
                            <TextField
                                label='PIDs'
                                variant='outlined'
                                size='small'
                                value={filters.pids}
                                onChange={(e) => updateFilter('pids', e.target.value)}
                                placeholder='Filter by PIDs...'
                            />

                            {/* IP Address Filter */}
                            <TextField
                                label='IP Address'
                                variant='outlined'
                                size='small'
                                value={filters.ip}
                                onChange={(e) => updateFilter('ip', e.target.value)}
                                placeholder='Filter by IP address...'
                            />

                            {/* Command Type Filter */}
                            <FormControl size='small'>
                                <InputLabel>Command Type</InputLabel>
                                <Select
                                    value={filters.commandType}
                                    onChange={(e) => updateFilter('commandType', e.target.value)}
                                    label='Command Type'>
                                    <MenuItem value=''>All</MenuItem>
                                    <MenuItem value='start'>start</MenuItem>
                                    <MenuItem value='stop'>stop</MenuItem>
                                </Select>
                            </FormControl>

                            {/* Status Filter */}
                            <FormControl size='small'>
                                <InputLabel>Profiling Status</InputLabel>
                                <Select
                                    value={filters.status}
                                    onChange={(e) => updateFilter('status', e.target.value)}
                                    label='Profiling Status'>
                                    <MenuItem value=''>All</MenuItem>
                                    <MenuItem value='pending'>pending</MenuItem>
                                    <MenuItem value='running'>running</MenuItem>
                                    <MenuItem value='completed'>completed</MenuItem>
                                    <MenuItem value='failed'>failed</MenuItem>
                                    <MenuItem value='stopped'>stopped</MenuItem>
                                </Select>
                            </FormControl>

                            {/* Clear Filters Button */}
                            <Box
                                sx={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    justifyContent: 'flex-end',
                                    gridColumn: '1 / -1',
                                }}>
                                <Button
                                    variant='outlined'
                                    size='small'
                                    onClick={() => {
                                        const emptyFilters = {
                                            service: '',
                                            hostname: '',
                                            pids: '',
                                            ip: '',
                                            commandType: '',
                                            status: '',
                                        };
                                        setFilters(emptyFilters);
                                        updateURL(emptyFilters);
                                    }}>
                                    Clear All Filters
                                </Button>
                            </Box>
                        </Box>
                    </Collapse>
                </Box>

                {/* Action Buttons */}
                <Box sx={{ mb: 3, display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
                    <Button
                        variant='contained'
                        color='success'
                        size='small'
                        onClick={() => handleBulkAction('start')}
                        disabled={selectionModel.length === 0}>
                        Start ({selectionModel.length})
                    </Button>
                    <Button
                        variant='contained'
                        color='error'
                        size='small'
                        onClick={() => handleBulkAction('stop')}
                        disabled={selectionModel.length === 0}>
                        Stop ({selectionModel.length})
                    </Button>
                    <Button
                        variant='outlined'
                        color='primary'
                        size='small'
                        onClick={() => fetchProfilingStatus(filters)}
                        disabled={loading}>
                        Refresh
                    </Button>
                    <Typography variant='body2' color='text.secondary'>
                        {rows.length} hosts found
                    </Typography>
                </Box>

                {/* Data Table */}
                <Box sx={{ '& .MuiDataGrid-root': { border: 'none' } }}>
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
            </Box>
        </>
    );
};

export default ProfilingStatusPage;
