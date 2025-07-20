package main

import (
	"bytes"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
	"time"
)

func main() {
	serverURL := "http://192.168.2.16:8000/upload" // Change as needed
	filePath := "/tmp/test_upload.bin"             // Change as needed

	// Create a test file if it doesn't exist
	const fileSize = 500 * 1024 * 1024 // 500MB
	if stat, err := os.Stat(filePath); err != nil || stat.Size() < fileSize {
		fmt.Printf("Creating test file of %d MB at %s ...\n", fileSize/(1024*1024), filePath)
		f, _ := os.Create(filePath)
		defer f.Close()
		buf := make([]byte, 1024*1024)
		for i := 0; i < fileSize/len(buf); i++ {
			f.Write(buf)
		}
	}

	file, err := os.Open(filePath)
	if err != nil {
		panic(err)
	}
	defer file.Close()

	body := &bytes.Buffer{}
	writer := multipart.NewWriter(body)
	part, err := writer.CreateFormFile("file", filepath.Base(filePath))
	if err != nil {
		panic(err)
	}

	start := time.Now()
	written, err := io.Copy(part, file)
	if err != nil {
		panic(err)
	}
	writer.Close()

	req, err := http.NewRequest("POST", serverURL, body)
	if err != nil {
		panic(err)
	}
	req.Header.Set("Content-Type", writer.FormDataContentType())

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		panic(err)
	}
	defer resp.Body.Close()
	elapsed := time.Since(start).Seconds()
	fmt.Printf("Uploaded %d MB in %.2fs (%.2f MB/s)\n", written/(1024*1024), elapsed, float64(written)/(1024*1024)/elapsed)
	io.Copy(os.Stdout, resp.Body)
}
