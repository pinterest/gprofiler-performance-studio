import React, { useState, useEffect } from 'react';
import MuiTable from '../common/dataDisplay/table/MuiTable';
import { 
  Button, 
  Box, 
  Typography, 
  FormControlLabel,
  Checkbox,
  Grid,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions
} from '@mui/material';
import TextField from '@mui/material/TextField';
import PageHeader from '../common/layout/PageHeader';

const ProfilingStatusPage = () => {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selectionModel, setSelectionModel] = useState([]);
  const [filter, setFilter] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [currentModalRow, setCurrentModalRow] = useState(null);
  const [selectedPids, setSelectedPids] = useState({}); // { rowId: [selectedPids] }

  // Open PID selection modal
  const openPidModal = (row) => {
    setCurrentModalRow(row);
    setModalOpen(true);
    
    // Auto-select all PIDs when opening modal if none are selected
    if (!selectedPids[row.id] || selectedPids[row.id].length === 0) {
      if (row.available_pids && row.available_pids.length > 0) {
        setSelectedPids(prev => ({
          ...prev,
          [row.id]: [...row.available_pids]
        }));
      }
    }
  };

  const closePidModal = () => {
    setModalOpen(false);
    setCurrentModalRow(null);
  };

  const columns = [
    { field: 'service', headerName: 'service name', flex: 1 },
    { field: 'host', headerName: 'host name', flex: 1 },
    { field: 'pids', headerName: 'available pids', flex: 1 },
    { field: 'ip', headerName: 'IP', flex: 1 },
    { field: 'commandType', headerName: 'command type', flex: 1 },
    { field: 'status', headerName: 'profiling status', flex: 1 },
    {
      field: 'selectedPids',
      headerName: 'Selected PIDs',
      flex: 1.2,
      renderCell: (params) => {
        const rowSelectedPids = selectedPids[params.row.id] || [];
        const allAvailablePids = params.row.available_pids || [];
        
        if (rowSelectedPids.length === 0) {
          return <Typography variant="body2" color="text.secondary">None</Typography>;
        }
        
        // Check if all PIDs are selected (host-level)
        if (rowSelectedPids.length === allAvailablePids.length) {
          return (
            <Typography variant="body2" color="success.main" sx={{ fontWeight: 'medium' }}>
              All PIDs (Host-level)
            </Typography>
          );
        }
        
        // Some PIDs selected (process-level)
        return (
          <Typography variant="body2" title={rowSelectedPids.join(', ')} color="primary">
            {rowSelectedPids.length > 3 
              ? `${rowSelectedPids.slice(0, 3).join(', ')}... (+${rowSelectedPids.length - 3} more)`
              : rowSelectedPids.join(', ')
            }
          </Typography>
        );
      }
    },
    { 
      field: 'selectPids', 
      headerName: 'Select PIDs', 
      width: 120,
      renderCell: (params) => (
        <Button
          size="small"
          variant="outlined"
          onClick={() => openPidModal(params.row)}
          disabled={!params.row.available_pids || params.row.available_pids.length === 0}
        >
          Select
        </Button>
      )
    }
  ];

  // Handle individual PID selection
  const handlePidSelection = (rowId, pid, checked) => {
    setSelectedPids(prev => {
      const currentSelection = prev[rowId] || [];
      if (checked) {
        return { ...prev, [rowId]: [...currentSelection, pid] };
      } else {
        return { ...prev, [rowId]: currentSelection.filter(p => p !== pid) };
      }
    });
  };

  // Handle select all PIDs for a row
  const handleSelectAllPids = (rowId, checked) => {
    const row = rows.find(r => r.id === rowId);
    if (row && row.available_pids) {
      setSelectedPids(prev => ({
        ...prev,
        [rowId]: checked ? [...row.available_pids] : []
      }));
    }
  };

  // Fetch profiling status from backend
  const fetchProfilingStatus = () => {
    setLoading(true);
    fetch('/api/metrics/profiling/host_status')
      .then(res => res.json())
      .then(data => {
        const mappedRows = data.map(row => ({
          id: row.id,
          service: row.service_name,
          host: row.hostname,
          pids: row.pids,
          ip: row.ip_address,
          commandType: row.command_type || 'N/A',
          status: row.profiling_status,
          available_pids: row.available_pids || []
        }));
        setRows(mappedRows);
        
        // Initialize selectedPids with all available PIDs for each row
        const initialSelections = {};
        for (const r of mappedRows) {
          if (r.available_pids && r.available_pids.length > 0) {
            initialSelections[r.id] = [...r.available_pids];
          } else {
            initialSelections[r.id] = [];
          }
        }
        setSelectedPids(initialSelections);

        setLoading(false);
      })
      .catch(() => setLoading(false));
  };

  useEffect(() => {
    fetchProfilingStatus();
  }, []);

  // Bulk Start/Stop handlers - now includes PID-level selections
  function handleBulkAction(action) {
    const selectedRows = rows.filter(row => selectionModel.includes(row.id));
    
    // Group selected rows by service name, avoiding duplicate hosts
    const serviceGroups = selectedRows.reduce((groups, row) => {
      if (!groups[row.service]) {
        groups[row.service] = {};
      }
      
      // Determine if this is host-level or process-level profiling
      const rowSelectedPids = selectedPids[row.id] || [];
      const allAvailablePids = row.available_pids || [];
      
      if (rowSelectedPids.length === 0) {
        // No PIDs selected - host level profiling
        groups[row.service][row.host] = null;
      } else if (rowSelectedPids.length === allAvailablePids.length) {
        // All PIDs selected - host level profiling
        groups[row.service][row.host] = null;
      } else {
        // Some (but not all) PIDs selected - process level profiling
        groups[row.service][row.host] = rowSelectedPids;
      }
      
      return groups;
    }, {});

    // Create one request per service with all hosts and their PIDs for that service
    const requests = Object.entries(serviceGroups).map(([serviceName, hostPidMapping]) => {
      const submitData = {
        service_name: serviceName,
        request_type: action,
        continuous: true,
        duration: 60,           // Default duration, can't be adjusted yet
        frequency: 11,          // Default frequency, can't be adjusted yet
        profiling_mode: 'cpu',  // Default profiling mode, can't be adjusted yet
        target_hosts: hostPidMapping,
      };

      // append 'stop_level: host' when action is 'stop'
      if (action === 'stop') {
        // Determine stop level based on PID selection
        const hasSpecificPids = Object.values(hostPidMapping).some(pids => pids !== null && pids.length > 0);
        submitData.stop_level = hasSpecificPids ? 'process' : 'host';
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
      setSelectionModel([]); // Clear all checkboxes after API requests complete
      setSelectedPids({}); // Clear PID selections
    });
  }

  const filteredRows = filter
    ? rows.filter(row => row.service.toLowerCase().includes(filter.toLowerCase()))
    : rows;

  // PID Selection Modal
  const renderPidSelectionModal = () => {
    if (!currentModalRow) return null;

    const rowSelectedPids = selectedPids[currentModalRow.id] || [];
    const allSelected = rowSelectedPids.length === currentModalRow.available_pids.length;
    const someSelected = rowSelectedPids.length > 0;

    return (
      <Dialog 
        open={modalOpen} 
        onClose={closePidModal}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>
          Select PIDs for {currentModalRow.host} ({currentModalRow.service})
        </DialogTitle>
        <DialogContent>
          <Box sx={{ mb: 2 }}>
            <FormControlLabel
              control={
                <Checkbox
                  checked={allSelected}
                  indeterminate={someSelected && !allSelected}
                  onChange={(e) => handleSelectAllPids(currentModalRow.id, e.target.checked)}
                />
              }
              label="Select All PIDs"
            />
          </Box>
          
          <Grid container spacing={2}>
            {currentModalRow.available_pids.map(pid => (
              <Grid item key={pid} xs={6} sm={4} md={3}>
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={rowSelectedPids.includes(pid)}
                      onChange={(e) => handlePidSelection(currentModalRow.id, pid, e.target.checked)}
                    />
                  }
                  label={`PID ${pid}`}
                />
              </Grid>
            ))}
          </Grid>
          
          {rowSelectedPids.length > 0 && (
            <Box sx={{ mt: 2, p: 2, bgcolor: 'grey.100', borderRadius: 1 }}>
              {rowSelectedPids.length === currentModalRow.available_pids.length ? (
                <Typography variant="body2" color="success.main" sx={{ fontWeight: 'medium' }}>
                  All PIDs selected ({rowSelectedPids.length}) → Host-level profiling
                </Typography>
              ) : (
                <Typography variant="body2" color="primary">
                  Selected PIDs ({rowSelectedPids.length}): {rowSelectedPids.join(', ')} → Process-level profiling
                </Typography>
              )}
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={closePidModal} color="primary">
            Close
          </Button>
        </DialogActions>
      </Dialog>
    );
  };

  return (
    <>
      <PageHeader title='Dynamic Profiling' />
      <Box sx={{ p: 3 }}>
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
            disabled={selectionModel.length === 0 || filteredRows.filter(r => selectionModel.includes(r.id) && (r.commandType == 'N/A' || (r.commandType === 'stop' && r.status === 'completed'))).length === 0}
          >
            Start
          </Button>
          <Button
            variant="contained"
            color="error"
            size="small"
            onClick={() => handleBulkAction('stop')}
            disabled={selectionModel.length === 0 || filteredRows.filter(r => selectionModel.includes(r.id) && r.commandType === 'start' && r.status === 'completed').length === 0}
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
        
        {/* PID Selection Modal */}
        {renderPidSelectionModal()}
      </Box>
    </>
  );
};

export default ProfilingStatusPage;