import { Box, Button, Divider, Typography, FormControlLabel, Checkbox, Tooltip, TextField } from '@mui/material';
import React from 'react';

import { COLORS } from '../../../theme/colors';
import Flexbox from '../../common/layout/Flexbox';

const PanelDivider = () => <Divider orientation='vertical' sx={{ borderColor: 'grey.dark', opacity: 0.1 }} flexItem />;

const ProfilingTopPanel = ({
    selectionModel,
    handleBulkAction,
    fetchProfilingStatus,
    filters,
    loading,
    rowsCount,
    clearAllFilters,
    enablePerfSpect,
    onPerfSpectChange,
    profilingFrequency,
    onProfilingFrequencyChange,
}) => {
    const hasActiveFilters = Object.values(filters).some((value) => value);

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
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, pl: 4 }}>
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
                    </Flexbox>

                    {/* Right side - Info and Clear Filters */}
                    <Flexbox spacing={3} alignItems='center' divider={<PanelDivider />}>
                        {hasActiveFilters && (
                            <Button variant='outlined' color='secondary' size='small' onClick={clearAllFilters}>
                                Clear Filters
                            </Button>
                        )}
                        <Typography variant='body2' color='text.secondary'>
                            {rowsCount} hosts found
                        </Typography>
                    </Flexbox>
                </Flexbox>
            </Box>
            {/* Additional panel space - matching ProfilesPage structure */}
            <Flexbox sx={{ px: 5, width: '100%' }} spacing={3}>
                {/* This area can be used for additional controls in the future */}
            </Flexbox>
        </Flexbox>
    );
};

export default ProfilingTopPanel;
