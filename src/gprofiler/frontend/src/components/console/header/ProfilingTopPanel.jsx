import { Box, Button, Divider, Typography, FormControlLabel, Checkbox, Tooltip, TextField, Radio, RadioGroup, Accordion, AccordionSummary, AccordionDetails } from '@mui/material';
import React from 'react';

import { COLORS } from '../../../theme/colors';
import Flexbox from '../../common/layout/Flexbox';

// Constants
const DEFAULT_PROFILING_FREQUENCY = 11; // Hz - industry standard for low overhead
const DEFAULT_MAX_PROCESSES = 10;
const DEFAULT_DURATION = 60; // seconds
const CONTINUOUS_MODE_DURATION = 60; // Fixed duration for continuous mode

const PanelDivider = () => <Divider orientation='vertical' sx={{ borderColor: 'grey.dark', opacity: 0.1 }} flexItem />;

const ProfilingTopPanel = ({
    selectionModel,
    handleBulkAction,
    fetchProfilingStatus,
    filters,
    loading,
    activeCount,
    totalCount,
    clearAllFilters,
    enablePerfSpect,
    onPerfSpectChange,
    profilingFrequency,
    onProfilingFrequencyChange,
    maxProcesses,
    onMaxProcessesChange,
    profilingMode,
    onProfilingModeChange,
    duration,
    onDurationChange,
    profilerConfigs,
    onProfilerConfigsChange,
}) => {
    const hasActiveFilters = Object.values(filters).some((value) => value);

    // Helper function to handle profiler config changes
    const handleProfilerConfigChange = (profilerKey, value) => {
        onProfilerConfigsChange(prev => ({
            ...prev,
            [profilerKey]: value
        }));
    };

    // Helper function to handle async profiler nested config changes
    const handleAsyncProfilerConfigChange = (field, value) => {
        onProfilerConfigsChange(prev => ({
            ...prev,
            async_profiler: {
                ...prev.async_profiler,
                [field]: value
            }
        }));
    };

    // Helper function to handle perf profiler nested config changes
    const handlePerfProfilerConfigChange = (field, value) => {
        onProfilerConfigsChange(prev => ({
            ...prev,
            perf: {
                ...prev.perf,
                [field]: value
            }
        }));
    };

    // Helper function to toggle perf events
    const handlePerfEventToggle = (eventName, isChecked) => {
        const currentEvents = profilerConfigs.perf?.events || [];
        const newEvents = isChecked
            ? [...currentEvents, eventName]
            : currentEvents.filter(ev => ev !== eventName);
        handlePerfProfilerConfigChange('events', newEvents);
    };

    // Perf event configuration
    const perfEvents = [
        { value: 'cpu-cycles', label: 'CPU Cycles', tooltip: 'Total processor cycles executed' },
        { value: 'instructions', label: 'Instructions', tooltip: 'Instructions executed by the CPU' },
        { value: 'cache-misses', label: 'Cache Misses', tooltip: 'Failed cache lookups - indicates memory access issues' },
        { value: 'cache-references', label: 'Cache References', tooltip: 'Total cache access attempts' },
        { value: 'branch-instructions', label: 'Branch Instructions', tooltip: 'Conditional jump instructions executed' },
        { value: 'branch-misses', label: 'Branch Misses', tooltip: 'Mispredicted branches - impacts pipeline efficiency' },
        { value: 'stalled-cycles-frontend', label: 'Stalled Cycles (Frontend)', tooltip: 'CPU cycles stalled waiting for instructions' },
        { value: 'stalled-cycles-backend', label: 'Stalled Cycles (Backend)', tooltip: 'CPU cycles stalled during execution (CPU-dependent)' }
    ];

    // Profiler configuration definitions
    const profilerDefinitions = [
        {
            key: 'perf',
            name: 'Perf Profiler',
            description: 'C, C++, Go, Kernel',
            options: [
                { value: 'enabled_restricted', label: 'Enabled Restricted', tooltip: 'Profiles only top N containers/process', default: true },
                { value: 'enabled_aggressive', label: 'Enabled Aggressive', tooltip: 'Profiles all processes' },
                { value: 'disabled', label: 'Disabled' }
            ]
        },
        {
            key: 'pyperf',
            name: 'Pyperf',
            description: "Python's highly optimized eBPF. Exception - arm64 hosts",
            options: [
                { value: 'enabled', label: 'Enabled', default: true },
                { value: 'disabled', label: 'Disabled' }
            ]
        },
        {
            key: 'pyspy',
            name: 'Pyspy',
            description: 'Python',
            options: [
                { value: 'enabled_fallback', label: 'Enabled as Fallback for Pyperf', default: true },
                { value: 'enabled', label: 'Enabled' },
                { value: 'disabled', label: 'Disabled' }
            ]
        },
        {
            key: 'rbspy',
            name: 'Rbspy',
            description: 'Ruby',
            options: [
                { value: 'enabled', label: 'Enabled' },
                { value: 'disabled', label: 'Disabled', default: true }
            ]
        },
        {
            key: 'phpspy',
            name: 'PHPspy',
            description: 'PHP',
            options: [
                { value: 'enabled', label: 'Enabled' },
                { value: 'disabled', label: 'Disabled', default: true }
            ]
        },
        {
            key: 'dotnet_trace',
            name: '.NET Trace',
            description: '.NET',
            options: [
                { value: 'enabled', label: 'Enabled' },
                { value: 'disabled', label: 'Disabled', default: true }
            ]
        },
        {
            key: 'nodejs_perf',
            name: 'Perf',
            description: 'NodeJS',
            options: [
                { value: 'enabled', label: 'Enabled', default: true },
                { value: 'disabled', label: 'Disabled' }
            ]
        }
    ];

    return (
        <Flexbox column spacing={2}>
            <Box
                sx={{
                    background: `linear-gradient(180deg,${COLORS.ALMOST_WHITE} 50%, ${COLORS.WHITE} 50%)`,
                    px: 4,
                    zIndex: 1,
                }}>
                <Flexbox
                    spacing={4}
                    justifyContent='space-between'
                    alignItems='center'
                    sx={{
                        height: '45px',
                        width: '100%',
                        backgroundColor: 'white.main',
                        boxShadow: '0px 8px 12px rgba(9, 30, 66, 0.15), 0px 0px 1px rgba(9, 30, 66, 0.31)',
                        borderRadius: '26px',
                        px: 5,
                    }}>
                    {/* Left side - Action Buttons */}
                    <Flexbox alignItems='center' spacing={2} divider={<PanelDivider />}>
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
                        
                        {/* PerfSpect Hardware Metrics Checkbox */}
                        <Tooltip title="Enable Intel PerfSpect hardware metrics collection (auto-installs on agents)">
                            <FormControlLabel
                                control={
                                    <Checkbox
                                        checked={enablePerfSpect}
                                        onChange={(e) => onPerfSpectChange(e.target.checked)}
                                        size="small"
                                        color="primary"
                                    />
                                }
                                label={
                                    <Typography variant="body2" sx={{ fontSize: '0.875rem' }}>
                                        PerfSpect HW Metrics
                                    </Typography>
                                }
                                sx={{ ml: 1 }}
                            />
                        </Tooltip>
                        
                        {/* Profiling Frequency Field */}
                        <Tooltip title="Number of samples per second for profiling duration (Hz)">
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, pl: 5 }}>
                                <Typography variant="body2" sx={{ fontSize: '0.875rem', whiteSpace: 'nowrap' }}>
                                    Profiling Frequency:
                                </Typography>
                                <TextField
                                    value={profilingFrequency}
                                    onChange={(e) => {
                                        const value = parseInt(e.target.value, 10);
                                        if (!isNaN(value) && value > 0 && value <= 1000) {
                                            onProfilingFrequencyChange(value);
                                        }
                                    }}
                                    type="number"
                                    size="small"
                                    inputProps={{
                                        min: 1,
                                        max: 1000,
                                        style: { textAlign: 'center' }
                                    }}
                                    sx={{
                                        width: '70px',
                                        '& .MuiOutlinedInput-root': {
                                            height: '32px',
                                            fontSize: '0.875rem'
                                        }
                                    }}
                                />
                                <Typography variant="body2" sx={{ fontSize: '0.75rem', color: 'text.secondary' }}>
                                    Hz
                                </Typography>
                            </Box>
                        </Tooltip>
                        
                        {/* Max Processes Field */}
                        <Tooltip title="Maximum number of processes to profile per runtime profiler (all profilers except perf and ebpf)">
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, pl: 5 }}>
                                <Typography variant="body2" sx={{ fontSize: '0.875rem', whiteSpace: 'nowrap' }}>
                                    Max Processes:
                                </Typography>
                                <TextField
                                    value={maxProcesses}
                                    onChange={(e) => {
                                        const value = parseInt(e.target.value, 10);
                                        if (!isNaN(value) && value >= 0 && value <= 1000) {
                                            onMaxProcessesChange(value);
                                        }
                                    }}
                                    type="number"
                                    size="small"
                                    inputProps={{
                                        min: 0,
                                        max: 1000,
                                        style: { textAlign: 'center' }
                                    }}
                                    sx={{
                                        width: '70px',
                                        '& .MuiOutlinedInput-root': {
                                            height: '32px',
                                            fontSize: '0.875rem'
                                        }
                                    }}
                                />
                                <Typography variant="body2" sx={{ fontSize: '0.75rem', color: 'text.secondary' }}>
                                    procs
                                </Typography>
                            </Box>
                        </Tooltip>
                    </Flexbox>
                    
                    {/* Profiling Mode and Duration Section */}
                    <Flexbox alignItems='center' spacing={2}>
                        {/* Profiling Mode Radio Buttons */}
                        <Tooltip title="Select profiling mode: Continuous for ongoing profiling (60s fixed), or Ad Hoc for one-time profiling with custom duration">
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                                <Typography variant="body2" sx={{ fontSize: '0.875rem', whiteSpace: 'nowrap' }}>
                                    Mode:
                                </Typography>
                                <RadioGroup
                                    row
                                    value={profilingMode}
                                    onChange={(e) => onProfilingModeChange(e.target.value)}
                                    sx={{ gap: 1, ml: 1 }}
                                >
                                    <FormControlLabel
                                        value="continuous"
                                        control={<Radio size="small" />}
                                        label={
                                            <Typography variant="body2" sx={{ fontSize: '0.875rem' }}>
                                                Continuous
                                            </Typography>
                                        }
                                    />
                                    <FormControlLabel
                                        value="adhoc"
                                        control={<Radio size="small" />}
                                        label={
                                            <Typography variant="body2" sx={{ fontSize: '0.875rem' }}>
                                                Ad Hoc
                                            </Typography>
                                        }
                                    />
                                </RadioGroup>
                            </Box>
                        </Tooltip>
                        
                        {/* Duration Field */}
                        <Tooltip title={profilingMode === 'continuous' ? 'Duration is fixed at 60 seconds for Continuous mode' : 'Profiling duration in seconds (only editable in Ad Hoc mode)'}>
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                                <Typography variant="body2" sx={{ fontSize: '0.875rem', whiteSpace: 'nowrap' }}>
                                    Duration:
                                </Typography>
                                <TextField
                                    value={profilingMode === 'continuous' ? 60 : duration}
                                    onChange={(e) => {
                                        const value = parseInt(e.target.value, 10);
                                        if (!isNaN(value) && value > 0 && value <= 3600) {
                                            onDurationChange(value);
                                        }
                                    }}
                                    type="number"
                                    size="small"
                                    disabled={profilingMode === 'continuous'}
                                    inputProps={{
                                        min: 1,
                                        max: 3600,
                                        style: { textAlign: 'center' }
                                    }}
                                    sx={{
                                        width: '70px',
                                        '& .MuiOutlinedInput-root': {
                                            height: '32px',
                                            fontSize: '0.875rem',
                                            backgroundColor: profilingMode === 'continuous' ? '#f5f5f5' : 'white'
                                        }
                                    }}
                                />
                                <Typography variant="body2" sx={{ fontSize: '0.75rem', color: 'text.secondary' }}>
                                    sec
                                </Typography>
                            </Box>
                        </Tooltip>
                    </Flexbox>

                    {/* Right side - Info and Clear Filters */}
                    <Flexbox spacing={3} alignItems='center' divider={<PanelDivider />}>
                        {hasActiveFilters && (
                            <Button variant='outlined' color='secondary' size='small' onClick={clearAllFilters}>
                                Clear Filters
                            </Button>
                        )}
                        <Typography variant='body2' color='text.secondary' sx={{ fontSize: '0.8rem' }}>
                            {activeCount} active hosts of {totalCount} hosts
                        </Typography>
                    </Flexbox>
                </Flexbox>
            </Box>
            {/* Profiler Configuration Section */}
            <Box sx={{ px: 5, width: '100%' }}>
                <Accordion sx={{ boxShadow: 'none', border: '1px solid #e0e0e0' }}>
                    <AccordionSummary
                        expandIcon={<Typography sx={{ fontSize: '1.2rem' }}>▼</Typography>}
                        sx={{ 
                            backgroundColor: '#f5f5f5',
                            '& .MuiAccordionSummary-content': {
                                alignItems: 'center'
                            }
                        }}
                    >
                        <Typography variant="body2" sx={{ fontSize: '0.875rem', fontWeight: 500 }}>
                            Click for Advanced Profiler Configuration
                        </Typography>
                    </AccordionSummary>
                    <AccordionDetails sx={{ p: 3 }}>
                        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 3 }}>
                            {/* Render Perf Profiler first with Event Selector */}
                            <Box sx={{ 
                                    border: '1px solid #e0e0e0', 
                                    borderRadius: 2, 
                                    p: 2,
                                    backgroundColor: '#fafafa'
                                }}>
                                    <Typography variant="body2" sx={{ fontSize: '0.875rem', fontWeight: 600, mb: 0.5 }}>
                                    Perf Profiler
                                    </Typography>
                                    <Typography variant="body2" sx={{ fontSize: '0.75rem', color: 'text.secondary', mb: 2 }}>
                                    C, C++, Go, Kernel
                                    </Typography>
                                
                                {/* Mode Selection */}
                                    <RadioGroup
                                    value={profilerConfigs.perf?.mode || 'enabled_restricted'}
                                    onChange={(e) => handlePerfProfilerConfigChange('mode', e.target.value)}
                                >
                                    <Tooltip 
                                        title="Profiles only top N containers/process" 
                                        placement="right"
                                        arrow
                                    >
                                        <FormControlLabel
                                            value="enabled_restricted"
                                            control={<Radio size="small" />}
                                            label={
                                                <Typography variant="body2" sx={{ fontSize: '0.75rem' }}>
                                                    Enabled Restricted
                                                </Typography>
                                            }
                                            sx={{ mb: 0.5 }}
                                        />
                                    </Tooltip>
                                    <Tooltip 
                                        title="Profiles all processes" 
                                        placement="right"
                                        arrow
                                    >
                                        <FormControlLabel
                                            value="enabled_aggressive"
                                            control={<Radio size="small" />}
                                            label={
                                                <Typography variant="body2" sx={{ fontSize: '0.75rem' }}>
                                                    Enabled Aggressive
                                                </Typography>
                                            }
                                            sx={{ mb: 0.5 }}
                                        />
                                    </Tooltip>
                                    <FormControlLabel
                                        value="disabled"
                                        control={<Radio size="small" />}
                                        label={
                                            <Typography variant="body2" sx={{ fontSize: '0.75rem' }}>
                                                Disabled
                                            </Typography>
                                        }
                                        sx={{ mb: 0.5 }}
                                    />
                                </RadioGroup>
                                
                                {/* Event Selection - Only show when perf is enabled */}
                                {profilerConfigs.perf?.mode !== 'disabled' && (
                                    <Box sx={{ ml: 3, mt: 2 }}>
                                        <Typography variant="body2" sx={{ fontSize: '0.75rem', fontWeight: 500, mb: 1 }}>
                                            Event Types (select one or more):
                                        </Typography>
                                        <Box sx={{ display: 'flex', flexDirection: 'column' }}>
                                            {perfEvents.map(event => (
                                                <Tooltip 
                                                    key={event.value}
                                                    title={event.tooltip} 
                                                    placement="right"
                                                    arrow
                                                >
                                                    <FormControlLabel
                                                        control={
                                                            <Checkbox
                                                                checked={profilerConfigs.perf?.events?.includes(event.value) || false}
                                                                onChange={(e) => handlePerfEventToggle(event.value, e.target.checked)}
                                                                size="small"
                                                            />
                                                        }
                                                        label={
                                                            <Typography variant="body2" sx={{ fontSize: '0.75rem' }}>
                                                                {event.label}
                                                            </Typography>
                                                        }
                                                        sx={{ mb: 0.5 }}
                                                    />
                                                </Tooltip>
                                            ))}
                                        </Box>
                                    </Box>
                                )}
                                </Box>
                            
                            {/* Custom Async Profiler Configuration - Right after Perf */}
                            <Box sx={{ 
                                border: '1px solid #e0e0e0', 
                                borderRadius: 2, 
                                p: 2,
                                backgroundColor: '#fafafa'
                            }}>
                                <Typography variant="body2" sx={{ fontSize: '0.875rem', fontWeight: 600, mb: 0.5 }}>
                                    Async Profiler
                                </Typography>
                                <Typography variant="body2" sx={{ fontSize: '0.75rem', color: 'text.secondary', mb: 2 }}>
                                    Java
                                </Typography>
                                
                                {/* Enable/Disable Toggle */}
                                <FormControlLabel
                                    control={
                                        <Checkbox
                                            checked={profilerConfigs.async_profiler?.enabled || false}
                                            onChange={(e) => handleAsyncProfilerConfigChange('enabled', e.target.checked)}
                                            size="small"
                                        />
                                    }
                                    label={
                                        <Typography variant="body2" sx={{ fontSize: '0.75rem' }}>
                                            Enabled
                                        </Typography>
                                    }
                                    sx={{ mb: 1 }}
                                />
                                
                                {/* Time Mode Selection */}
                                {profilerConfigs.async_profiler?.enabled && (
                                    <Box sx={{ ml: 3 }}>
                                        <Typography variant="body2" sx={{ fontSize: '0.75rem', fontWeight: 500, mb: 1 }}>
                                            Time Mode:
                                        </Typography>
                                        <RadioGroup
                                            value={profilerConfigs.async_profiler?.time || 'cpu'}
                                            onChange={(e) => handleAsyncProfilerConfigChange('time', e.target.value)}
                                        >
                                            <Tooltip 
                                                title="CPU time profiling - only when thread is running" 
                                                placement="right"
                                                arrow
                                            >
                                                <FormControlLabel
                                                    value="cpu"
                                                    control={<Radio size="small" />}
                                                    label={
                                                        <Typography variant="body2" sx={{ fontSize: '0.75rem' }}>
                                                            CPU Time
                                                        </Typography>
                                                    }
                                                    sx={{ mb: 0.5 }}
                                                />
                                            </Tooltip>
                                            <Tooltip 
                                                title="Wall clock time profiling - includes waiting/blocking time" 
                                                placement="right"
                                                arrow
                                            >
                                                <FormControlLabel
                                                    value="wall"
                                                    control={<Radio size="small" />}
                                                    label={
                                                        <Typography variant="body2" sx={{ fontSize: '0.75rem' }}>
                                                            Wall Time
                                                        </Typography>
                                                    }
                                                    sx={{ mb: 0.5 }}
                                                />
                                            </Tooltip>
                                        </RadioGroup>
                                    </Box>
                                )}
                            </Box>
                            
                            {/* Render remaining profilers (excluding perf) */}
                            {profilerDefinitions.filter(p => p.key !== 'perf').map((profiler) => (
                                <Box key={profiler.key} sx={{ 
                                    border: '1px solid #e0e0e0', 
                                    borderRadius: 2, 
                                    p: 2,
                                    backgroundColor: '#fafafa'
                                }}>
                                    <Typography variant="body2" sx={{ fontSize: '0.875rem', fontWeight: 600, mb: 0.5 }}>
                                        {profiler.name}
                                    </Typography>
                                    <Typography variant="body2" sx={{ fontSize: '0.75rem', color: 'text.secondary', mb: 2 }}>
                                        {profiler.description}
                                    </Typography>
                                    <RadioGroup
                                        value={profilerConfigs[profiler.key]}
                                        onChange={(e) => handleProfilerConfigChange(profiler.key, e.target.value)}
                                    >
                                        {profiler.options.map((option) => (
                                            <Tooltip 
                                                key={option.value} 
                                                title={option.tooltip || ''} 
                                                placement="right"
                                                arrow
                                            >
                                                <FormControlLabel
                                                    value={option.value}
                                                    control={<Radio size="small" />}
                                                    label={
                                                        <Typography variant="body2" sx={{ fontSize: '0.75rem' }}>
                                                            {option.label}
                                                        </Typography>
                                                    }
                                                    sx={{ mb: 0.5 }}
                                                />
                                            </Tooltip>
                                        ))}
                                    </RadioGroup>
                                </Box>
                            ))}
                        </Box>
                    </AccordionDetails>
                </Accordion>
            </Box>
        </Flexbox>
    );
};

export default ProfilingTopPanel;
