import React, { useState, useEffect } from 'react';
import MuiTable from '../common/dataDisplay/table/MuiTable';
import { Button, Box, Typography, duration } from '@mui/material';
import TextField from '@mui/material/TextField';

const columns = [
  { field: 'service', headerName: 'service name', flex: 1 },
  { field: 'host', headerName: 'host name', flex: 1 },
  { field: 'pids', headerName: 'pids (if profiled)', flex: 1 },
  { field: 'ip', headerName: 'IP', flex: 1 },
  { field: 'status', headerName: 'profiling status', flex: 1 },
];

const ProfilingStatusPage = () => {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selectionModel, setSelectionModel] = useState([]);
  const [filter, setFilter] = useState('');

  // Placeholder: Fetch profiling status from backend
  const fetchProfilingStatus = () => {
    setLoading(true);
    fetch('/api/metrics/profiling/host_status')
      .then(res => res.json())
      .then(data => {
        setRows(
          data.map(row => ({
            id: row.id,
            service: row.service_name,
            host: row.hostname,
            pids: row.pids,
            ip: row.ip_address,
            status: row.profiling_status,
          }))
        );
        setLoading(false);
      })
      .catch(() => setLoading(false));
  };

  useEffect(() => {
    fetchProfilingStatus();
  }, []);

  // Bulk Start/Stop handlers
  function handleBulkAction(action) {
    const selectedRows = rows.filter(row => selectionModel.includes(row.id));
    
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
      fetchProfilingStatus();
    });
  }

  const filteredRows = filter
    ? rows.filter(row => row.service.toLowerCase().includes(filter.toLowerCase()))
    : rows;

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h5" gutterBottom>
        Dynamic Profiling
      </Typography>
      <Box sx={{ mb: 2, display: 'flex', gap: 2, alignItems: 'center' }}>
        <TextField
          label="Filter by service name"
          variant="outlined"
          size="small"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          sx={{ minWidth: 250 }}
        />
        <Button
          variant="contained"
          color="success"
          size="small"
          onClick={() => handleBulkAction('start')}
          disabled={selectionModel.length === 0 || filteredRows.filter(r => selectionModel.includes(r.id) && r.status === 'Running').length === selectionModel.length}
        >
          Start
        </Button>
        <Button
          variant="contained"
          color="error"
          size="small"
          onClick={() => handleBulkAction('stop')}
          disabled={selectionModel.length === 0 || filteredRows.filter(r => selectionModel.includes(r.id) && (r.status === 'Pending' || r.status === 'Running')).length === 0}
        >
          Stop
        </Button>
        <Button
          variant="outlined"
          color="primary"
          size="small"
          onClick={fetchProfilingStatus}
          disabled={loading}
        >
          Refresh
        </Button>
      </Box>
      <MuiTable
        columns={columns}
        data={filteredRows}
        isLoading={loading}
        pageSize={50}
        rowHeight={50}
        autoPageSize
        checkboxSelection
        onSelectionModelChange={setSelectionModel}
        selectionModel={selectionModel}
      />
    </Box>
  );
};

export default ProfilingStatusPage;