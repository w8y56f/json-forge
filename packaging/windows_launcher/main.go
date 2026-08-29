//go:build windows

// JSON Forge Windows self-extracting launcher.
package main

import (
	"archive/zip"
	"bytes"
	_ "embed"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"unsafe"
)

//go:embed payload.zip
var payload []byte

var payloadVersion = "development"

const payloadRoot = "json-forge-windows-x86_64"

func showError(message string) {
	user32 := syscall.NewLazyDLL("user32.dll")
	messageBox := user32.NewProc("MessageBoxW")
	text, _ := syscall.UTF16PtrFromString(message)
	title, _ := syscall.UTF16PtrFromString("JSON Forge")
	messageBox.Call(0, uintptr(unsafePointer(text)), uintptr(unsafePointer(title)), 0x10)
}

// unsafePointer keeps the Windows API call isolated from the rest of the launcher.
func unsafePointer(value *uint16) unsafe.Pointer {
	return unsafe.Pointer(value)
}

func extractPayload(destination string) error {
	marker := filepath.Join(destination, ".complete")
	if _, err := os.Stat(marker); err == nil {
		return nil
	}

	temporary := destination + ".installing"
	if err := os.RemoveAll(temporary); err != nil {
		return err
	}
	if err := os.MkdirAll(temporary, 0o755); err != nil {
		return err
	}

	archive, err := zip.NewReader(bytes.NewReader(payload), int64(len(payload)))
	if err != nil {
		return err
	}
	prefix := filepath.Clean(temporary) + string(os.PathSeparator)
	for _, entry := range archive.File {
		name := filepath.FromSlash(entry.Name)
		parts := strings.SplitN(name, string(os.PathSeparator), 2)
		if len(parts) != 2 || parts[0] != payloadRoot {
			continue
		}
		target := filepath.Join(temporary, parts[1])
		if !strings.HasPrefix(filepath.Clean(target), prefix) {
			return fmt.Errorf("unsafe path in embedded package: %s", entry.Name)
		}
		if entry.FileInfo().IsDir() {
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		source, err := entry.Open()
		if err != nil {
			return err
		}
		destinationFile, err := os.OpenFile(target, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, entry.Mode())
		if err != nil {
			source.Close()
			return err
		}
		_, copyErr := io.Copy(destinationFile, source)
		closeErr := destinationFile.Close()
		source.Close()
		if copyErr != nil {
			return copyErr
		}
		if closeErr != nil {
			return closeErr
		}
	}
	if err := os.WriteFile(filepath.Join(temporary, ".complete"), []byte(payloadVersion+"\n"), 0o644); err != nil {
		return err
	}
	if err := os.RemoveAll(destination); err != nil {
		return err
	}
	return os.Rename(temporary, destination)
}

func launch(root string) error {
	python := filepath.Join(root, "runtime", "python", "pythonw.exe")
	application := filepath.Join(root, "app.py")
	if _, err := os.Stat(python); err != nil {
		return fmt.Errorf("bundled Python was not found: %w", err)
	}
	dataRoot := os.Getenv("APPDATA")
	if dataRoot == "" {
		dataRoot = os.Getenv("LOCALAPPDATA")
	}
	dataRoot = filepath.Join(dataRoot, "JSON Forge")
	command := exec.Command(python, append([]string{application}, os.Args[1:]...)...)
	command.Dir = root
	command.Env = append(os.Environ(),
		"JSON_STUDIO_SETTINGS_PATH="+filepath.Join(dataRoot, "config", "settings.ini"),
		"JSON_STUDIO_SESSION_PATH="+filepath.Join(dataRoot, "cache", "session.json"),
		"JSON_STUDIO_INSTANCE_LOCK_PATH="+filepath.Join(dataRoot, "cache", "json-forge.lock"),
	)
	return command.Start()
}

func run() error {
	localData := os.Getenv("LOCALAPPDATA")
	if localData == "" {
		return fmt.Errorf("LOCALAPPDATA is not available")
	}
	root := filepath.Join(localData, "JSON Forge", "runtime-"+payloadVersion)
	if err := extractPayload(root); err != nil {
		return fmt.Errorf("unable to install bundled files: %w", err)
	}
	return launch(root)
}

func main() {
	if err := run(); err != nil {
		showError(err.Error())
	}
}
