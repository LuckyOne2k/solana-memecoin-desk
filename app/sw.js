/*
 * Service Worker: hält nur die App-Oberfläche offline bereit.
 * Scan-Daten (latest.json) werden hier NIE zwischengespeichert; Anfragen an
 * andere Server laufen unverändert durch.
 */
"use strict";

const CACHE = "desk-app-v7";
const SHELL = [
  "./",
  "index.html",
  "style.css",
  "app.js",
  "paper.js",
  "trend.js",
  "manifest.webmanifest",
  "icon-192.png",
  "icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  // Nur eigene Dateien der App; Anfragen an andere Server nicht anfassen.
  if (req.method !== "GET" || url.origin !== self.location.origin) return;

  // Zuerst Netz (immer aktuelle App), bei fehlender Verbindung die gespeicherte Kopie.
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req, { ignoreSearch: true }).then((hit) => hit || caches.match("index.html")))
  );
});
