import { Box, Typography } from '@mui/material';
import queryString from 'query-string';
import React, { useCallback, useEffect, useState } from 'react';
import { useHistory, useLocation } from 'react-router-dom';

import { DATA_URLS } from '../../api/urls';
import MuiTable from '../common/dataDisplay/table/MuiTable';
import PageHeader from '../common/layout/PageHeader';
import ProfilingHeader from './header/ProfilingHeader';
import ProfilingTopPanel from './header/ProfilingTopPanel';

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

    // Initialize filters from URL parameters for direct URL visits (shareable links)
    useEffect(() => {
        const searchParams = queryString.parse(location.search);
        const hasFilterParams = ['service', 'hostname', 'pids', 'ip', 'commandType', 'status'].some(
            param => searchParams[param]
        );
        
        // Only initialize from URL if there are actual filter parameters
        if (hasFilterParams) {
            const urlFilters = {
                service: searchParams.service || '',
                hostname: searchParams.hostname || '',
                pids: searchParams.pids || '',
                ip: searchParams.ip || '',
                commandType: searchParams.commandType || '',
                status: searchParams.status || '',
            };
            setFilters(urlFilters);
        }
        
        // Clean up profile-specific parameters if they exist (mixed URLs)
        const profileParams = ['gtab', 'view', 'time', 'startTime', 'endTime', 'filter', 'rt', 'rtms', 'p', 'pm', 'wt', 'wp', 'search', 'fullscreen'];
        const hasProfileParams = profileParams.some(param => searchParams[param]);
        
        if (hasProfileParams) {
            // Remove only profile params, keep filter params
            const cleanedParams = { ...searchParams };
            profileParams.forEach(param => {
                delete cleanedParams[param];
            });
            history.replace({ search: queryString.stringify(cleanedParams) });
        }
    }, []); // Only run once on mount

    // Update URL when filters change
    const updateURL = useCallback(
        (newFilters) => {
            // Start with empty search params since we want clean filter updates
            const searchParams = {};

            // Add new filter parameters
            Object.keys(newFilters).forEach((key) => {
                if (newFilters[key]) {
                    searchParams[key] = newFilters[key];
                }
            });

            const newSearch = queryString.stringify(searchParams);
            
            // If search string is empty, use explicit pathname to clear URL
            if (newSearch === '') {
                history.push('/profiling');
            } else {
                history.push({ pathname: '/profiling', search: newSearch });
            }
        },
        [history]
    );

    const fetchProfilingStatus = useCallback((filterParams) => {
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
    }, []); // No dependencies needed since it takes filterParams as argument

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

    // Clear all filters function
    const clearAllFilters = () => {
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
        <Box sx={{ backgroundColor: 'white.main', height: '100%' }}>
            <ProfilingHeader filters={filters} updateFilter={updateFilter} isLoading={loading} />

            <Box
                sx={{
                    display: 'flex',
                    flexDirection: 'column',
                }}>
                <ProfilingTopPanel
                    selectionModel={selectionModel}
                    handleBulkAction={handleBulkAction}
                    fetchProfilingStatus={fetchProfilingStatus}
                    filters={filters}
                    loading={loading}
                    rowsCount={rows.length}
                    clearAllFilters={clearAllFilters}
                />

                {/* Data Table */}
                <Box sx={{ p: 4, '& .MuiDataGrid-root': { border: 'none' } }}>
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
        </Box>
    );
};

export default ProfilingStatusPage;
