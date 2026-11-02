// Smartphone-Belegerfassung: Seiten aufnehmen, prüfen, drehen, zuschneiden, verkleinern und
// einzeln übertragen. Bei Verbindungsabbruch bleiben die Seiten im Browser (IndexedDB) und die
// Übertragung wird beim nächsten Öffnen fortgesetzt. Ohne JavaScript funktioniert das Formular
// als normaler Upload mehrerer Dateien. Keine externen Bibliotheken, keine Inline-Skripte.
(function () {
  "use strict";
  var form = document.getElementById("belegformular");
  if (!form || !window.fetch || !window.createImageBitmap || !window.indexedDB || !window.crypto || !crypto.subtle) return;

  var input = document.getElementById("seiten");
  var liste = document.getElementById("vorschau");
  var status = document.getElementById("status");
  var knopf = document.getElementById("absenden");
  var auftrag = form.dataset.auftrag;
  var maxSeiten = parseInt(form.dataset.maxSeiten, 10) || 30;
  var csrf = form.querySelector("input[name=csrfmiddlewaretoken]").value;
  var MAX_KANTE = 2400, ZIEL_BYTES = 2.5 * 1024 * 1024;
  var seiten = [];
  var zaehler = 0;

  input.removeAttribute("name"); // Dateien gehen per fetch, nicht mit dem Formular
  input.addEventListener("change", function () {
    var dateien = Array.prototype.slice.call(input.files);
    input.value = "";
    hinzufuegen(dateien, null);
  });

  // --- Seiten verwalten ---------------------------------------------------------------
  function hinzufuegen(dateien, ersetzen) {
    dateien.forEach(function (datei) {
      if (!ersetzen && seiten.length >= maxSeiten) { melden("Höchstens " + maxSeiten + " Seiten je Beleg."); return; }
      var s = { id: ++zaehler, datei: datei, pdf: datei.type === "application/pdf", drehung: 0,
                schnitt: { o: 0, u: 0, l: 0, r: 0 }, bild: null, blob: null, sha: "", vorschau: "" };
      if (ersetzen) { seiten[seiten.indexOf(ersetzen)] = s; } else { seiten.push(s); }
      vorbereiten(s);
    });
  }

  function vorbereiten(s) {
    if (s.pdf) { s.blob = s.datei; zeichnen(); return Promise.resolve(); }
    return createImageBitmap(s.datei).then(function (bild) { s.bild = bild; return rendern(s); })
      .catch(function () { melden("Ein Foto konnte nicht gelesen werden. Bitte erneut aufnehmen."); });
  }

  function rendern(s) {
    var b = s.bild, w = b.width, h = b.height;
    var sx = w * s.schnitt.l / 100, sy = h * s.schnitt.o / 100;
    var sw = Math.max(1, w * (100 - s.schnitt.l - s.schnitt.r) / 100), sh = Math.max(1, h * (100 - s.schnitt.o - s.schnitt.u) / 100);
    var faktor = Math.min(1, MAX_KANTE / Math.max(sw, sh));
    var tw = Math.round(sw * faktor), th = Math.round(sh * faktor);
    var quer = s.drehung % 180 !== 0;
    var c = document.createElement("canvas");
    c.width = quer ? th : tw; c.height = quer ? tw : th;
    var ctx = c.getContext("2d");
    ctx.translate(c.width / 2, c.height / 2);
    ctx.rotate(s.drehung * Math.PI / 180);
    ctx.drawImage(b, sx, sy, sw, sh, -tw / 2, -th / 2, tw, th);
    return komprimieren(c, 0.85).then(function (blob) {
      s.blob = blob;
      var v = document.createElement("canvas"), f = 240 / Math.max(c.width, c.height);
      v.width = Math.round(c.width * f); v.height = Math.round(c.height * f);
      v.getContext("2d").drawImage(c, 0, 0, v.width, v.height);
      s.vorschau = v.toDataURL("image/jpeg", 0.7);
      zeichnen();
    });
  }

  function komprimieren(c, q) {
    return new Promise(function (ok) { c.toBlob(ok, "image/jpeg", q); }).then(function (blob) {
      return (blob.size > ZIEL_BYTES && q > 0.55) ? komprimieren(c, q - 0.1) : blob;
    });
  }

  function knopfMit(text, aktion) {
    var k = document.createElement("button");
    k.type = "button"; k.className = "btn ghost small"; k.textContent = text;
    k.addEventListener("click", aktion);
    return k;
  }

  function zeichnen() {
    liste.textContent = "";
    seiten.forEach(function (s, i) {
      var el = document.createElement("div");
      el.className = "seite";
      var kopf = document.createElement("p");
      kopf.className = "meta";
      kopf.textContent = "Seite " + (i + 1) + (s.blob ? " · " + Math.round(s.blob.size / 1024) + " KB" : " · wird vorbereitet");
      el.appendChild(kopf);
      if (s.vorschau) {
        var img = document.createElement("img");
        img.src = s.vorschau; img.alt = "Vorschau Seite " + (i + 1); img.className = "seitenbild";
        el.appendChild(img);
      } else if (s.pdf) {
        var p = document.createElement("p"); p.textContent = "PDF: " + s.datei.name; el.appendChild(p);
      }
      var aktionen = document.createElement("div");
      aktionen.className = "row";
      if (!s.pdf) {
        aktionen.appendChild(knopfMit("Drehen", function () { s.drehung = (s.drehung + 90) % 360; rendern(s); }));
        aktionen.appendChild(knopfMit("Zuschneiden", function () { s.schnittOffen = !s.schnittOffen; schnittEl.hidden = !s.schnittOffen; }));
      }
      aktionen.appendChild(knopfMit("Neu aufnehmen", function () {
        var neu = document.createElement("input");
        neu.type = "file"; neu.accept = "image/jpeg,image/png,application/pdf"; neu.setAttribute("capture", "environment");
        neu.addEventListener("change", function () { if (neu.files[0]) hinzufuegen([neu.files[0]], s); });
        neu.click();
      }));
      aktionen.appendChild(knopfMit("Entfernen", function () { seiten.splice(seiten.indexOf(s), 1); zeichnen(); }));
      el.appendChild(aktionen);
      var schnittEl = document.createElement("div");
      schnittEl.hidden = !s.schnittOffen; schnittEl.className = "schnitt";
      [["o", "oben"], ["u", "unten"], ["l", "links"], ["r", "rechts"]].forEach(function (k) {
        var l = document.createElement("label");
        l.textContent = "Rand " + k[1] + " abschneiden (%)";
        var r = document.createElement("input");
        r.type = "range"; r.min = "0"; r.max = "40"; r.value = String(s.schnitt[k[0]]);
        r.addEventListener("change", function () { s.schnitt[k[0]] = parseInt(r.value, 10); rendern(s).then(function () {}); });
        l.appendChild(r); schnittEl.appendChild(l);
      });
      el.appendChild(schnittEl);
      liste.appendChild(el);
    });
  }

  function melden(text) { status.textContent = text; }

  // --- Zwischenspeicher für Wiederaufnahme ------------------------------------------------
  function db() {
    return new Promise(function (ok, fehler) {
      var r = indexedDB.open("zahlenwerk-belege", 1);
      r.onupgradeneeded = function () { r.result.createObjectStore("vorgaenge", { keyPath: "auftrag" }); };
      r.onsuccess = function () { ok(r.result); };
      r.onerror = function () { fehler(r.error); };
    });
  }
  function speicher(modus, aktion) {
    return db().then(function (d) {
      return new Promise(function (ok, fehler) {
        var t = d.transaction("vorgaenge", modus), st = t.objectStore("vorgaenge");
        var r = aktion(st);
        t.oncomplete = function () { ok(r && r.result); };
        t.onerror = function () { fehler(t.error); };
      });
    });
  }
  var merken = function (v) { return speicher("readwrite", function (st) { return st.put(v); }); };
  var laden = function () { return speicher("readonly", function (st) { return st.get(auftrag); }); };
  var vergessen = function () { return speicher("readwrite", function (st) { return st.delete(auftrag); }); };

  function sha256(blob) {
    return blob.arrayBuffer().then(function (b) { return crypto.subtle.digest("SHA-256", b); }).then(function (h) {
      return Array.prototype.map.call(new Uint8Array(h), function (x) { return ("0" + x.toString(16)).slice(-2); }).join("");
    });
  }

  function senden(url, daten, versuch) {
    versuch = versuch || 1;
    daten.append("csrfmiddlewaretoken", csrf);
    return fetch(url, { method: "POST", body: daten, credentials: "same-origin", headers: { "X-CSRFToken": csrf } })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (r.ok) return j;
          var e = new Error(j.fehler || ("Fehler " + r.status)); e.endgueltig = r.status >= 400 && r.status < 500; throw e;
        });
      })
      .catch(function (e) {
        if (e.endgueltig || versuch >= 3) throw e;
        return new Promise(function (ok) { setTimeout(ok, 1500 * versuch); }).then(function () {
          var neu = new FormData(); daten.forEach(function (v, k) { if (k !== "csrfmiddlewaretoken") neu.append(k, v); });
          return senden(url, neu, versuch + 1);
        });
      });
  }

  function uebertragen(v) {
    knopf.disabled = true;
    var kette = Promise.resolve();
    v.seiten.forEach(function (s) {
      kette = kette.then(function () {
        if (s.fertig) return;
        melden("Übertrage Seite " + s.seite + " von " + v.seiten.length + " …");
        var d = new FormData();
        d.append("seite", String(s.seite)); d.append("sha256", s.sha);
        d.append("datei", s.blob, s.name);
        return senden(v.seiteUrl, d).then(function () { s.fertig = true; return merken(v); });
      });
    });
    return kette.then(function () {
      melden("Alle Seiten übertragen. Beleg wird abgeschlossen …");
      return senden(v.abschlussUrl, new FormData());
    }).then(function (j) {
      return vergessen().then(function () { melden("Beleg ist eingegangen."); window.location.assign(j.weiter); });
    }).catch(function (e) {
      knopf.disabled = false;
      melden("Übertragung unterbrochen: " + e.message + " Die Seiten bleiben gespeichert. Bitte erneut auf „Beleg senden“ tippen.");
      fortsetzen = v;
    });
  }

  var fortsetzen = null;
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    if (fortsetzen) { uebertragen(fortsetzen); return; }
    var fertig = seiten.filter(function (s) { return s.blob; });
    if (!fertig.length || fertig.length !== seiten.length) { melden("Bitte zuerst alle Seiten aufnehmen und warten, bis die Vorschau erscheint."); return; }
    knopf.disabled = true;
    var d = new FormData(); d.append("hinweis", document.getElementById("hinweis").value);
    Promise.all(fertig.map(function (s) { return sha256(s.blob); })).then(function (shas) {
      return senden(form.dataset.vorgangUrl, d).then(function (j) {
        var v = { auftrag: auftrag, vorgang: j.vorgang,
                  seiteUrl: "/beleg/" + j.vorgang + "/seite/", abschlussUrl: "/beleg/" + j.vorgang + "/abschliessen/",
                  seiten: fertig.map(function (s, i) {
                    return { seite: i + 1, sha: shas[i], blob: s.blob, name: "seite-" + (i + 1) + (s.pdf ? ".pdf" : ".jpg"), fertig: false };
                  }) };
        return merken(v).then(function () { return uebertragen(v); });
      });
    }).catch(function (e) { knopf.disabled = false; melden("Der Beleg konnte nicht angelegt werden: " + e.message); });
  });

  laden().then(function (v) {
    if (v && v.seiten && v.seiten.some(function (s) { return !s.fertig; })) {
      fortsetzen = v;
      melden("Ein unterbrochener Beleg mit " + v.seiten.length + " Seite(n) wurde gefunden. Tippen Sie auf „Beleg senden“, um die Übertragung fortzusetzen.");
    }
  }).catch(function () {});
})();
