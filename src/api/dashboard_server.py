import http.server
import socketserver
import json
import sqlite3
import os
import unicodedata
from urllib.parse import urlparse, parse_qs

PROJECT_META = {"title": "26년 지방선거 분석 엔진", "id": "2026_local_election", "db_file": "hub.db"}
PORT = 8000
BASE_DIR = "/Users/up_main/Desktop/T_Antigravity/PTPoll"
DB_PATH = os.path.join(BASE_DIR, "data", PROJECT_META["id"], PROJECT_META["db_file"])

import sys
sys_path = os.path.join(BASE_DIR, "src", "engine")
if sys_path not in sys.path: sys.path.append(sys_path)
from simulation_engine import ScenarioSimulator

def normalize_ko(text):
    if not text: return text
    return unicodedata.normalize('NFC', text)

class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urlparse(self.path)
        query_params = parse_qs(parsed_path.query)

        if parsed_path.path == '/api/project':
            self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps(PROJECT_META).encode())

        elif parsed_path.path == '/api/meta':
            self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT name, properties FROM objects WHERE obj_type='SEGMENT'")
            meta = {"REGION": [], "AGE": [], "GENDER": [], "dates": []}
            for name, props in cursor.fetchall():
                p = json.loads(props); cat = p.get('category', 'ETC')
                if cat in meta: meta[cat].append(normalize_ko(name))
            cursor.execute("SELECT properties FROM objects WHERE obj_type='POLL'")
            for row in cursor.fetchall():
                p = json.loads(row[0]); meta['dates'].append(p.get('date'))
            meta['dates'].sort(); conn.close()
            self.wfile.write(json.dumps(meta).encode())

        elif parsed_path.path == '/api/trends':
            segment = normalize_ko(query_params.get('segment', ['전국'])[0])
            self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT p.properties, l.properties FROM links l 
                JOIN objects p ON l.source_id = p.id JOIN objects o ON l.target_id = o.id
                WHERE l.link_type = 'MEASURES_IN_SEGMENT' AND o.name = ?
            ''', (segment,))
            trends = {"dates": [], "candidates": {}}
            for p_props, l_props in cursor.fetchall():
                p, l = json.loads(p_props), json.loads(l_props)
                date = p.get('date')
                if date not in trends["dates"]: trends["dates"].append(date)
                for cand, rate in l.items():
                    if cand not in trends["candidates"]: trends["candidates"][cand] = []
                    trends["candidates"][cand].append(rate)
            trends["dates"].sort(); conn.close()
            self.wfile.write(json.dumps(trends).encode())

        elif parsed_path.path == '/api/simulate':
            segment = normalize_ko(query_params.get('segment', ['전국'])[0])
            impact = float(query_params.get('impact', [0])[0])
            simulator = ScenarioSimulator(DB_PATH)
            result = simulator.run_simulation(segment, impact)
            self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps(result).encode())
            
        elif parsed_path.path == '/':
            self.send_response(200); self.send_header('Content-type', 'text/html; charset=utf-8'); self.end_headers()
            with open(os.path.join(os.path.dirname(__file__), 'index.html'), 'rb') as f:
                self.wfile.write(f.read())
        else:
            super().do_GET()

if __name__ == "__main__":
    with socketserver.TCPServer(("", PORT), DashboardHandler) as httpd:
        print(f"[*] Hub Server Fixed (NFC Aware) on http://localhost:{PORT}")
        httpd.serve_forever()
