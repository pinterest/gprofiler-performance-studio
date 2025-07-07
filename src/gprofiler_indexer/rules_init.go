package main

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// UpsertOptimizationRules runs the Python upsert script on startup
func UpsertOptimizationRules(args *CLIArgs) error {
	// Skip if disabled via CLI flag
	if args.SkipRulesInit {
		logger.Info("Skipping optimization rules initialization (disabled via --skip-rules-init)")
		return nil
	}
	
	logger.Info("Initializing optimization rules...")
	
	// Get the directory where the binary is located
	execPath, err := os.Executable()
	if err != nil {
		return fmt.Errorf("failed to get executable path: %w", err)
	}
	
	// Use custom config path if provided, otherwise use relative to binary
	var configPath string
	if filepath.IsAbs(args.RulesConfigPath) {
		configPath = args.RulesConfigPath
	} else {
		configPath = filepath.Join(filepath.Dir(execPath), args.RulesConfigPath)
	}
	
	scriptDir := filepath.Dir(configPath)
	scriptPath := filepath.Join(scriptDir, "upsert_optimization_rules.py")
	
	// Check if files exist
	if _, err := os.Stat(scriptPath); os.IsNotExist(err) {
		logger.Warnf("Upsert script not found at %s, skipping rule initialization", scriptPath)
		return nil
	}
	
	if _, err := os.Stat(configPath); os.IsNotExist(err) {
		logger.Warnf("Rules config not found at %s, skipping rule initialization", configPath)
		return nil
	}
	
	// Extract host from ClickHouseAddr (remove port if present)
	clickhouseHost := strings.Split(args.ClickHouseAddr, ":")[0]
	
	// Prepare command
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	
	cmdArgs := []string{
		scriptPath,
		"--config-file", configPath,
		"--clickhouse-host", clickhouseHost,
		"--user", args.ClickHouseUser,
		"--verbose",
	}
	
	// Add password if provided
	if args.ClickHousePassword != "" {
		cmdArgs = append(cmdArgs, "--password", args.ClickHousePassword)
	}
	
	cmd := exec.CommandContext(ctx, "python3", cmdArgs...)
	
	// Set working directory
	cmd.Dir = scriptDir
	
	// Capture output
	output, err := cmd.CombinedOutput()
	if err != nil {
		logger.Errorf("Failed to run upsert script: %v\nOutput: %s", err, string(output))
		// Don't return error - just warn, so startup continues
		logger.Warn("Continuing startup without optimization rules initialization")
		return nil
	}
	
	logger.Infof("Optimization rules initialized successfully:\n%s", string(output))
	return nil
}
