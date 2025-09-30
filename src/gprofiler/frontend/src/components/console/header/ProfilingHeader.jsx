import { Box, FormControl, InputLabel, MenuItem, Select, TextField } from '@mui/material';
import React from 'react';

import { COLORS } from '../../../theme/colors';
import Flexbox from '../../common/layout/Flexbox';

const ProfilingHeader = ({ filters, updateFilter, isLoading = false }) => {
    return (
        <Flexbox
            spacing={{ xs: 1, sm: 2, md: 4 }}
            justifyContent='start'
            sx={{ p: 4, zIndex: 3, background: COLORS.ALMOST_WHITE, position: 'relative' }}>
            <Box
                sx={{
                    display: 'grid',
                    gridTemplateColumns: { 
                        xs: '1fr', 
                        sm: '1fr 1fr', 
                        md: '1fr 1fr 1fr', 
                        lg: '1fr 1fr 1fr 1fr 1fr 1fr' 
                    },
                    width: '100%',
                    gap: { xs: 2, sm: 2, md: 3 },
                }}>
                {/* Service Name Filter */}
                <TextField
                    label='Service Name'
                    variant='outlined'
                    size='small'
                    value={filters.service}
                    onChange={(e) => updateFilter('service', e.target.value)}
                    placeholder='Filter by service...'
                    disabled={isLoading}
                    fullWidth
                    sx={{
                        backgroundColor: 'white !important',
                        borderRadius: '4px',
                        boxShadow: '0px 2px 4px rgba(0, 0, 0, 0.1)',
                        '& .MuiOutlinedInput-root': {
                            backgroundColor: 'white !important',
                            '& fieldset': {
                                borderColor: 'rgba(0, 0, 0, 0.23)',
                            },
                            '&:hover fieldset': {
                                borderColor: 'rgba(0, 0, 0, 0.87)',
                            },
                        },
                        '& .MuiInputBase-input': {
                            backgroundColor: 'white !important',
                        },
                    }}
                />

                {/* Hostname Filter */}
                <TextField
                    label='Hostname'
                    variant='outlined'
                    size='small'
                    value={filters.hostname}
                    onChange={(e) => updateFilter('hostname', e.target.value)}
                    placeholder='Filter by hostname...'
                    disabled={isLoading}
                    fullWidth
                    sx={{
                        backgroundColor: 'white !important',
                        borderRadius: '4px',
                        boxShadow: '0px 2px 4px rgba(0, 0, 0, 0.1)',
                        '& .MuiOutlinedInput-root': {
                            backgroundColor: 'white !important',
                            '& fieldset': {
                                borderColor: 'rgba(0, 0, 0, 0.23)',
                            },
                            '&:hover fieldset': {
                                borderColor: 'rgba(0, 0, 0, 0.87)',
                            },
                        },
                        '& .MuiInputBase-input': {
                            backgroundColor: 'white !important',
                        },
                    }}
                />

                {/* PIDs Filter */}
                <TextField
                    label='PIDs'
                    variant='outlined'
                    size='small'
                    value={filters.pids}
                    onChange={(e) => updateFilter('pids', e.target.value)}
                    placeholder='Filter by PIDs...'
                    disabled={isLoading}
                    fullWidth
                    sx={{
                        backgroundColor: 'white !important',
                        borderRadius: '4px',
                        boxShadow: '0px 2px 4px rgba(0, 0, 0, 0.1)',
                        '& .MuiOutlinedInput-root': {
                            backgroundColor: 'white !important',
                            '& fieldset': {
                                borderColor: 'rgba(0, 0, 0, 0.23)',
                            },
                            '&:hover fieldset': {
                                borderColor: 'rgba(0, 0, 0, 0.87)',
                            },
                        },
                        '& .MuiInputBase-input': {
                            backgroundColor: 'white !important',
                        },
                    }}
                />

                {/* IP Address Filter */}
                <TextField
                    label='IP Address'
                    variant='outlined'
                    size='small'
                    value={filters.ip}
                    onChange={(e) => updateFilter('ip', e.target.value)}
                    placeholder='Filter by IP...'
                    disabled={isLoading}
                    fullWidth
                    sx={{
                        backgroundColor: 'white !important',
                        borderRadius: '4px',
                        boxShadow: '0px 2px 4px rgba(0, 0, 0, 0.1)',
                        '& .MuiOutlinedInput-root': {
                            backgroundColor: 'white !important',
                            '& fieldset': {
                                borderColor: 'rgba(0, 0, 0, 0.23)',
                            },
                            '&:hover fieldset': {
                                borderColor: 'rgba(0, 0, 0, 0.87)',
                            },
                        },
                        '& .MuiInputBase-input': {
                            backgroundColor: 'white !important',
                        },
                    }}
                />

                {/* Command Type Filter */}
                <FormControl
                    size='small'
                    fullWidth
                    sx={{
                        backgroundColor: 'white !important',
                        borderRadius: '4px',
                        boxShadow: '0px 2px 4px rgba(0, 0, 0, 0.1)',
                        '& .MuiOutlinedInput-root': {
                            backgroundColor: 'white !important',
                            '& fieldset': {
                                borderColor: 'rgba(0, 0, 0, 0.23)',
                            },
                            '&:hover fieldset': {
                                borderColor: 'rgba(0, 0, 0, 0.87)',
                            },
                        },
                        '& .MuiSelect-select': {
                            backgroundColor: 'white !important',
                        },
                    }}>
                    <InputLabel>Command Type</InputLabel>
                    <Select
                        value={filters.commandType}
                        onChange={(e) => updateFilter('commandType', e.target.value)}
                        label='Command Type'
                        disabled={isLoading}>
                        <MenuItem value=''>All</MenuItem>
                        <MenuItem value='start'>start</MenuItem>
                        <MenuItem value='stop'>stop</MenuItem>
                    </Select>
                </FormControl>

                {/* Profiling Status Filter */}
                <FormControl
                    size='small'
                    fullWidth
                    sx={{
                        backgroundColor: 'white !important',
                        borderRadius: '4px',
                        boxShadow: '0px 2px 4px rgba(0, 0, 0, 0.1)',
                        '& .MuiOutlinedInput-root': {
                            backgroundColor: 'white !important',
                            '& fieldset': {
                                borderColor: 'rgba(0, 0, 0, 0.23)',
                            },
                            '&:hover fieldset': {
                                borderColor: 'rgba(0, 0, 0, 0.87)',
                            },
                        },
                        '& .MuiSelect-select': {
                            backgroundColor: 'white !important',
                        },
                    }}>
                    <InputLabel>Profiling Status</InputLabel>
                    <Select
                        value={filters.status}
                        onChange={(e) => updateFilter('status', e.target.value)}
                        label='Profiling Status'
                        disabled={isLoading}>
                        <MenuItem value=''>All</MenuItem>
                        <MenuItem value='pending'>pending</MenuItem>
                        <MenuItem value='running'>running</MenuItem>
                        <MenuItem value='completed'>completed</MenuItem>
                        <MenuItem value='failed'>failed</MenuItem>
                        <MenuItem value='stopped'>stopped</MenuItem>
                    </Select>
                </FormControl>
            </Box>
            <Flexbox sx={{ height: '42px' }}>
                {/* Additional actions area - matching ProfilesPage structure */}
                <Box />
            </Flexbox>
        </Flexbox>
    );
};

export default ProfilingHeader;
