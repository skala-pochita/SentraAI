package com.demo.shop.controller;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class FileController {

    @Value("${app.upload-dir}")
    private String baseDir;

    @GetMapping("/files/download")
    public ResponseEntity<byte[]> download(@RequestParam String filename) throws Exception {
        Path path = Paths.get(baseDir + "/" + filename);
        return ResponseEntity.ok(Files.readAllBytes(path));
    }
}
