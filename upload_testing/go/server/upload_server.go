package main

import (
    "fmt"
    "io"
    "log"
    "net/http"
    "os"
    "path/filepath"
    "time"
)

func uploadHandler(w http.ResponseWriter, r *http.Request) {
    start := time.Now()
    file, header, err := r.FormFile("file")
    if err != nil {
        http.Error(w, "Missing file: "+err.Error(), http.StatusBadRequest)
        return
    }
    defer file.Close()

    tmpDir := "/dev/shm"
    if stat, err := os.Stat(tmpDir); err != nil || !stat.IsDir() {
        tmpDir = os.TempDir()
    }
    tmpPath := filepath.Join(tmpDir, fmt.Sprintf("upload-%d-%s", time.Now().UnixNano(), header.Filename))
    out, err := os.Create(tmpPath)
    if err != nil {
        http.Error(w, "Failed to create temp file: "+err.Error(), http.StatusInternalServerError)
        return
    }
    defer func() {
        out.Close()
        os.Remove(tmpPath)
    }()

    written, err := io.Copy(out, file)
    if err != nil {
        http.Error(w, "Write error: "+err.Error(), http.StatusInternalServerError)
        return
    }
    elapsed := time.Since(start).Seconds()
    log.Printf("Received %d MB in %.2fs (%.2f MB/s)", written/(1024*1024), elapsed, float64(written)/(1024*1024)/elapsed)
    fmt.Fprintf(w, "OK: %d bytes in %.2fs (%.2f MB/s)\n", written, elapsed, float64(written)/(1024*1024)/elapsed)
}

func main() {
    http.HandleFunc("/upload", uploadHandler)
    log.Println("Go upload server listening on :8000")
    log.Fatal(http.ListenAndServe(":8000", nil))
}