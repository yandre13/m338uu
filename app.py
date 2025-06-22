from flask import Flask, request, jsonify
from flask_cors import CORS
import yt_dlp
import json
import re
import requests
from urllib.parse import urlparse
import tempfile
import os
import time

app = Flask(__name__)
CORS(app)

class YTDLPExtractor:
    def __init__(self):
        self.base_ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'skip_download': True,  # Solo extraer info, no descargar
        }
        # Directorios compatibles con Docker
        self.cookies_dir = os.path.join('/app', 'cookies')
        self.downloads_dir = os.path.join('/app', 'downloads')
        
        # Crear directorios si no existen
        os.makedirs(self.cookies_dir, exist_ok=True)
        os.makedirs(self.downloads_dir, exist_ok=True)
    
    def is_pcloud_link(self, url):
        """Detecta si es un enlace de pCloud"""
        return "u.pcloud.link/publink/show" in url
    
    def extract_pcloud_m3u8(self, pcloud_url):
        """Extrae la URL del m3u8 desde pCloud con manejo avanzado de IP"""
        try:
            # Crear una sesión para mantener cookies
            session = requests.Session()
            
            # Headers más completos para simular un navegador real
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Accept-Language': 'en-US,en;q=0.9,es;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'max-age=0',
                'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"'
            }
            
            print(f"Intentando acceder a pCloud: {pcloud_url}")
            
            # Estrategia 1: Acceso directo
            try:
                response = session.get(pcloud_url, headers=headers, timeout=30)
                if "generated for another IP address" not in response.text:
                    print("✓ Acceso directo exitoso")
                    return self._parse_pcloud_response(response, pcloud_url)
            except Exception as e:
                print(f"❌ Acceso directo falló: {e}")
            
            # Estrategia 2: Regenerar el enlace usando la API de pCloud
            try:
                print("🔄 Intentando regenerar enlace...")
                code = self._extract_pcloud_code(pcloud_url)
                if code:
                    new_url = self._regenerate_pcloud_link(session, code, headers)
                    if new_url and new_url != pcloud_url:
                        print(f"✓ Nuevo enlace generado: {new_url}")
                        response = session.get(new_url, headers=headers, timeout=30)
                        if "generated for another IP address" not in response.text:
                            return self._parse_pcloud_response(response, new_url)
            except Exception as e:
                print(f"❌ Regeneración falló: {e}")
            
            # Estrategia 3: Múltiples intentos con diferentes User-Agents
            user_agents = [
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15'
            ]
            
            for i, ua in enumerate(user_agents):
                try:
                    print(f"🔄 Intento {i+1} con diferente User-Agent...")
                    headers['User-Agent'] = ua
                    
                    # Crear nueva sesión para cada intento
                    new_session = requests.Session()
                    response = new_session.get(pcloud_url, headers=headers, timeout=30)
                    
                    if "generated for another IP address" not in response.text:
                        print(f"✓ Éxito con User-Agent {i+1}")
                        return self._parse_pcloud_response(response, pcloud_url)
                        
                except Exception as e:
                    print(f"❌ User-Agent {i+1} falló: {e}")
                    continue
            
            # Estrategia 4: Intentar con proxy headers simulados
            print("🔄 Intentando con proxy headers...")
            proxy_ips = ['8.8.8.8', '1.1.1.1', '208.67.222.222', '9.9.9.9']
            
            for ip in proxy_ips:
                try:
                    proxy_headers = headers.copy()
                    proxy_headers.update({
                        'X-Forwarded-For': ip,
                        'X-Real-IP': ip,
                        'CF-Connecting-IP': ip,
                        'X-Originating-IP': ip,
                        'X-Remote-IP': ip,
                        'X-Remote-Addr': ip
                    })
                    
                    new_session = requests.Session()
                    response = new_session.get(pcloud_url, headers=proxy_headers, timeout=30)
                    
                    if "generated for another IP address" not in response.text:
                        print(f"✓ Éxito con IP proxy: {ip}")
                        return self._parse_pcloud_response(response, pcloud_url)
                        
                except Exception as e:
                    continue
            
            # Si todas las estrategias fallan, dar instrucciones al usuario
            raise Exception("""
            🚫 No se pudo acceder al enlace de pCloud debido a restricciones de IP.
            
            SOLUCIONES POSIBLES:
            1. 📱 Accede al enlace desde tu navegador primero
            2. 🔄 Genera un nuevo enlace de pCloud desde tu cuenta
            3. 🌐 Usa una VPN y genera un nuevo enlace
            4. 📋 Copia las cookies de tu navegador y úsalas en la API
            
            El enlace fue generado para una IP diferente a la del servidor.
            """)
            
        except requests.RequestException as e:
            raise Exception(f"Error al acceder a pCloud: {str(e)}")
        except json.JSONDecodeError as e:
            raise Exception(f"Error al parsear JSON de pCloud: {str(e)}")
        except Exception as e:
            raise Exception(f"Error procesando pCloud: {str(e)}")
    
    def _extract_pcloud_code(self, url):
        """Extrae el código del enlace de pCloud"""
        import urllib.parse as urlparse
        parsed = urlparse.urlparse(url)
        query_params = urlparse.parse_qs(parsed.query)
        return query_params.get('code', [None])[0]
    
    def _regenerate_pcloud_link(self, session, code, headers):
        """Intenta regenerar el enlace de pCloud usando la API"""
        try:
            # Intentar obtener nueva URL usando la API pública
            api_url = f"https://api.pcloud.com/getpublinkdownload"
            params = {'code': code}
            
            response = session.get(api_url, params=params, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if 'hosts' in data and data['hosts']:
                    # Construir nueva URL
                    host = data['hosts'][0]
                    path = data.get('path', '')
                    if host and path:
                        return f"https://{host}{path}"
        except:
            pass
        return None
    
    def _parse_pcloud_response(self, response, pcloud_url):
        """Parsea la respuesta HTML de pCloud para extraer datos"""
        # Resto del código igual...
        json_pattern = r'var publinkData = ({.*?});'
        json_match = re.search(json_pattern, response.text, re.DOTALL)
        
        if not json_match:
            # Buscar patrones alternativos
            alt_patterns = [
                r'window\.publinkData = ({.*?});',
                r'publinkData = ({.*?});',
                r'"publinkData":\s*({.*?})',
            ]
            
            for pattern in alt_patterns:
                json_match = re.search(pattern, response.text, re.DOTALL)
                if json_match:
                    break
            
            if not json_match:
                raise Exception("No se pudo extraer publinkData del HTML")
        
        # Parsear el JSON
        data = json.loads(json_match.group(1))
        
        # Buscar la variante HLS (m3u8)
        variants = data.get('variants', [])
        hls_formats = []
        
        for variant in variants:
            if variant.get('transcodetype') == 'hls':
                path = variant['path']
                hosts = variant.get('hosts', [])
                
                if hosts:
                    host = hosts[0]
                    m3u8_url = f"https://{host}{path}"
                    
                    hls_format = {
                        'format_id': f"pcloud_hls_{variant.get('id', 'unknown')}",
                        'url': m3u8_url,
                        'ext': 'm3u8',
                        'protocol': 'm3u8_native',
                        'height': variant.get('height'),
                        'width': variant.get('width'),
                        'fps': variant.get('fps'),
                        'tbr': variant.get('bitrate'),
                        'format_note': f"pCloud HLS {variant.get('height', 'unknown')}p",
                        'referer': pcloud_url,
                        'expires': variant.get('expires'),
                        'host': host,
                        'source': 'pcloud'
                    }
                    hls_formats.append(hls_format)
        
        if not hls_formats:
            raise Exception("No se encontraron variantes HLS en los datos de pCloud")
        
        # Información básica del archivo
        basic_info = {
            'title': data.get('name', 'pCloud Video'),
            'duration': data.get('duration'),
            'filesize': data.get('size'),
            'thumbnail': data.get('thumb1024', data.get('thumb', '')),
            'uploader': 'pCloud',
            'webpage_url': pcloud_url,
            'source': 'pcloud'
        }
        
        return hls_formats, basic_info

    def save_cookies_file(self, cookies_content, filename):
        """Guarda cookies en un archivo temporal"""
        cookies_path = os.path.join(self.cookies_dir, filename)
        with open(cookies_path, 'w') as f:
            f.write(cookies_content)
        return cookies_path
    
    def prepare_ydl_opts(self, cookies_file=None, cookies_dict=None, headers=None):
        """Prepara opciones de yt-dlp con cookies y headers"""
        opts = self.base_ydl_opts.copy()
        
        # Agregar cookies desde archivo
        if cookies_file and os.path.exists(cookies_file):
            opts['cookiefile'] = cookies_file
        
        # Agregar headers personalizados
        if headers:
            opts['http_headers'] = headers
        
        # Si tenemos cookies como diccionario, las convertimos a archivo
        if cookies_dict:
            cookies_content = self.dict_to_netscape_cookies(cookies_dict)
            temp_file = f"temp_cookies_{int(time.time())}.txt"
            cookies_path = self.save_cookies_file(cookies_content, temp_file)
            opts['cookiefile'] = cookies_path
        
        return opts
    
    def dict_to_netscape_cookies(self, cookies_dict):
        """Convierte diccionario de cookies a formato Netscape"""
        lines = ["# Netscape HTTP Cookie File"]
        for name, value in cookies_dict.items():
            # Formato: domain, domain_specified, path, secure, expires, name, value
            line = f".example.com\tTRUE\t/\tFALSE\t0\t{name}\t{value}"
            lines.append(line)
        return '\n'.join(lines)
    
    def extract_info(self, url, extract_formats=True, cookies_file=None, cookies_dict=None, headers=None):
        """Extrae información del video usando yt-dlp o pCloud"""
        try:
            # Verificar si es un enlace de pCloud
            if self.is_pcloud_link(url):
                hls_formats, basic_info = self.extract_pcloud_m3u8(url)
                # Simular estructura de yt-dlp
                info = basic_info.copy()
                info['formats'] = hls_formats
                return info
            
            # Usar yt-dlp para otros sitios
            opts = self.prepare_ydl_opts(cookies_file, cookies_dict, headers)
            if not extract_formats:
                opts['extract_flat'] = True
            
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info
                
        except Exception as e:
            raise Exception(f"Error extracting info: {str(e)}")
    
    def get_hls_urls(self, url, cookies_file=None, cookies_dict=None, headers=None):
        """Extrae URLs HLS específicamente"""
        try:
            # Para pCloud, usar método específico
            if self.is_pcloud_link(url):
                hls_formats, basic_info = self.extract_pcloud_m3u8(url)
                return hls_formats, basic_info
            
            # Para otros sitios, usar yt-dlp
            info = self.extract_info(url, cookies_file=cookies_file, cookies_dict=cookies_dict, headers=headers)
            hls_formats = []
            
            if 'formats' in info:
                for fmt in info['formats']:
                    # Buscar formatos HLS
                    if fmt.get('protocol') == 'm3u8' or fmt.get('protocol') == 'm3u8_native':
                        hls_formats.append({
                            'format_id': fmt.get('format_id'),
                            'url': fmt.get('url'),
                            'ext': fmt.get('ext'),
                            'quality': fmt.get('quality'),
                            'height': fmt.get('height'),
                            'width': fmt.get('width'),
                            'fps': fmt.get('fps'),
                            'tbr': fmt.get('tbr'),  # Total bitrate
                            'protocol': fmt.get('protocol'),
                            'format_note': fmt.get('format_note')
                        })
                    
                    # También buscar URLs que contengan .m3u8
                    elif fmt.get('url') and '.m3u8' in fmt.get('url', ''):
                        hls_formats.append({
                            'format_id': fmt.get('format_id'),
                            'url': fmt.get('url'),
                            'ext': fmt.get('ext'),
                            'quality': fmt.get('quality'),
                            'height': fmt.get('height'),
                            'width': fmt.get('width'),
                            'fps': fmt.get('fps'),
                            'tbr': fmt.get('tbr'),
                            'protocol': fmt.get('protocol', 'http'),
                            'format_note': fmt.get('format_note'),
                            'detected': 'url_contains_m3u8'
                        })
            
            return hls_formats, info
        except Exception as e:
            raise Exception(f"Error getting HLS URLs: {str(e)}")
    
    def get_best_hls(self, url, cookies_file=None, cookies_dict=None, headers=None):
        """Obtiene la mejor calidad HLS disponible"""
        try:
            hls_formats, info = self.get_hls_urls(url, cookies_file, cookies_dict, headers)
            
            if not hls_formats:
                return None, info
            
            # Ordenar por calidad (height y tbr)
            best_format = max(hls_formats, key=lambda x: (
                x.get('height', 0),
                x.get('tbr', 0)
            ))
            
            return best_format, info
        except Exception as e:
            raise Exception(f"Error getting best HLS: {str(e)}")
    
    def get_supported_sites(self):
        """Lista sitios soportados por yt-dlp + pCloud"""
        try:
            with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                extractors = ydl.list_extractors()
                sites = [extractor.IE_NAME for extractor in extractors if hasattr(extractor, 'IE_NAME')]
                # Agregar pCloud a la lista
                sites.append('pCloud')
                return sites
        except Exception as e:
            return ['pCloud']  # Al menos devolver pCloud si falla

@app.route('/pcloud-helper', methods=['GET'])
def pcloud_helper():
    """Información de ayuda para enlaces de pCloud con problemas de IP"""
    return jsonify({
        'message': 'Ayuda para enlaces de pCloud con restricciones de IP',
        'problem': 'Este enlace fue generado para otra dirección IP',
        'solutions': [
            {
                'method': 'browser_access',
                'title': '1. Acceder desde el navegador primero',
                'description': 'Abre el enlace en tu navegador, luego extrae las cookies',
                'steps': [
                    'Abre el enlace de pCloud en tu navegador',
                    'Presiona F12 → Application → Cookies',
                    'Copia las cookies y úsalas en la API'
                ]
            },
            {
                'method': 'new_link',
                'title': '2. Generar nuevo enlace',
                'description': 'Desde tu cuenta de pCloud, genera un nuevo enlace público',
                'steps': [
                    'Inicia sesión en pCloud',
                    'Ve al archivo y genera un nuevo enlace público',
                    'Usa el nuevo enlace en la API'
                ]
            },
            {
                'method': 'cookies_api',
                'title': '3. Usar cookies con la API',
                'description': 'Envía las cookies de tu navegador junto con la URL',
                'example': {
                    'url': 'POST /extract',
                    'body': {
                        'url': 'tu_enlace_pcloud',
                        'cookies': {
                            'pcauth': 'valor_de_cookie',
                            'locationid': 'valor_de_cookie'
                        },
                        'headers': {
                            'Referer': 'https://u.pcloud.link/'
                        }
                    }
                }
            }
        ],
        'cookie_extraction_guide': {
            'chrome': [
                'F12 → Application → Storage → Cookies → u.pcloud.link',
                'Busca: pcauth, locationid, session_id'
            ],
            'firefox': [
                'F12 → Storage → Cookies → u.pcloud.link',
                'Busca: pcauth, locationid, session_id'
            ]
        }
    })

@app.route('/extract', methods=['POST'])
def extract_hls():
    """Extrae URLs HLS de un video (incluyendo pCloud)"""
    try:
        data = request.json
        url = data.get('url')
        best_only = data.get('best_only', False)
        
        # Opciones de autenticación
        cookies_file = data.get('cookies_file')  # Ruta al archivo de cookies
        cookies_dict = data.get('cookies')       # Diccionario de cookies
        headers = data.get('headers')            # Headers personalizados
        cookies_content = data.get('cookies_content')  # Contenido directo del archivo
        
        if not url:
            return jsonify({'error': 'URL is required'}), 400
        
        extractor = YTDLPExtractor()
        temp_cookies_file = None
        
        try:
            # Si se envió contenido de cookies, crear archivo temporal
            if cookies_content:
                temp_file = f"uploaded_cookies_{int(time.time())}.txt"
                temp_cookies_file = extractor.save_cookies_file(cookies_content, temp_file)
                cookies_file = temp_cookies_file
            
            if best_only:
                best_format, info = extractor.get_best_hls(url, cookies_file, cookies_dict, headers)
                hls_formats = [best_format] if best_format else []
            else:
                hls_formats, info = extractor.get_hls_urls(url, cookies_file, cookies_dict, headers)
            
            # Detectar si es pCloud
            is_pcloud = extractor.is_pcloud_link(url)
            
            return jsonify({
                'success': True,
                'url': url,
                'title': info.get('title', 'Unknown'),
                'duration': info.get('duration'),
                'uploader': info.get('uploader'),
                'hls_formats_count': len(hls_formats),
                'hls_formats': hls_formats,
                'thumbnail': info.get('thumbnail'),
                'used_cookies': bool(cookies_file or cookies_dict),
                'used_headers': bool(headers),
                'source': 'pcloud' if is_pcloud else 'yt-dlp',
                'is_pcloud': is_pcloud
            })
        
        finally:
            # Limpiar archivo temporal de cookies
            if temp_cookies_file and os.path.exists(temp_cookies_file):
                os.remove(temp_cookies_file)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/formats', methods=['POST'])
def get_all_formats():
    """Obtiene todos los formatos disponibles (incluyendo pCloud)"""
    try:
        data = request.json
        url = data.get('url')
        filter_protocol = data.get('protocol')  # 'm3u8', 'http', etc.
        
        if not url:
            return jsonify({'error': 'URL is required'}), 400
        
        extractor = YTDLPExtractor()
        info = extractor.extract_info(url)
        
        formats = []
        if 'formats' in info:
            for fmt in info['formats']:
                format_info = {
                    'format_id': fmt.get('format_id'),
                    'url': fmt.get('url'),
                    'ext': fmt.get('ext'),
                    'protocol': fmt.get('protocol'),
                    'quality': fmt.get('quality'),
                    'height': fmt.get('height'),
                    'width': fmt.get('width'),
                    'fps': fmt.get('fps'),
                    'tbr': fmt.get('tbr'),
                    'abr': fmt.get('abr'),
                    'vbr': fmt.get('vbr'),
                    'format_note': fmt.get('format_note'),
                    'filesize': fmt.get('filesize'),
                    'language': fmt.get('language'),
                    'referer': fmt.get('referer'),
                    'expires': fmt.get('expires'),
                    'host': fmt.get('host'),
                    'source': fmt.get('source')
                }
                
                # Filtrar por protocolo si se especifica
                if filter_protocol:
                    if fmt.get('protocol') == filter_protocol:
                        formats.append(format_info)
                else:
                    formats.append(format_info)
        
        # Detectar si es pCloud
        is_pcloud = extractor.is_pcloud_link(url)
        
        return jsonify({
            'success': True,
            'url': url,
            'title': info.get('title'),
            'formats_count': len(formats),
            'formats': formats,
            'is_pcloud': is_pcloud
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download', methods=['POST'])
def download_video():
    """Descarga el video (opcional) - No soportado para pCloud"""
    try:
        data = request.json
        url = data.get('url')
        format_id = data.get('format_id', 'best')
        output_path = data.get('output_path', './downloads')
        
        if not url:
            return jsonify({'error': 'URL is required'}), 400
        
        extractor = YTDLPExtractor()
        
        # Verificar si es pCloud
        if extractor.is_pcloud_link(url):
            return jsonify({
                'error': 'Download not supported for pCloud links. Use the HLS URL directly with your video player.'
            }), 400
        
        # Crear directorio si no existe
        os.makedirs(output_path, exist_ok=True)
        
        ydl_opts = {
            'format': format_id,
            'outtmpl': f'{output_path}/%(title)s.%(ext)s'
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
        return jsonify({
            'success': True,
            'title': info.get('title'),
            'downloaded': True,
            'output_path': output_path
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/upload-cookies', methods=['POST'])
def upload_cookies():
    """Sube un archivo de cookies para usar posteriormente"""
    try:
        if 'cookies_file' not in request.files:
            return jsonify({'error': 'No cookies file provided'}), 400
        
        file = request.files['cookies_file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        # Guardar archivo de cookies
        filename = f"cookies_{int(time.time())}_{file.filename}"
        cookies_path = os.path.join('./cookies', filename)
        file.save(cookies_path)
        
        return jsonify({
            'success': True,
            'cookies_id': filename,
            'message': 'Cookies file uploaded successfully',
            'path': cookies_path
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/cookies/<cookies_id>', methods=['DELETE'])
def delete_cookies(cookies_id):
    """Elimina un archivo de cookies"""
    try:
        cookies_path = os.path.join('./cookies', cookies_id)
        if os.path.exists(cookies_path):
            os.remove(cookies_path)
            return jsonify({
                'success': True,
                'message': 'Cookies file deleted successfully'
            })
        else:
            return jsonify({'error': 'Cookies file not found'}), 404
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/cookies', methods=['GET'])
def list_cookies():
    """Lista archivos de cookies disponibles"""
    try:
        cookies_files = []
        if os.path.exists('./cookies'):
            for filename in os.listdir('./cookies'):
                if filename.endswith('.txt'):
                    file_path = os.path.join('./cookies', filename)
                    file_stats = os.stat(file_path)
                    cookies_files.append({
                        'id': filename,
                        'size': file_stats.st_size,
                        'created': file_stats.st_ctime,
                        'modified': file_stats.st_mtime
                    })
        
        return jsonify({
            'success': True,
            'cookies_files': cookies_files,
            'count': len(cookies_files)
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        'message': 'yt-dlp HLS Extractor API with Cookies Support + pCloud',
        'endpoints': {
            'POST /extract': 'Extract HLS URLs from video (supports pCloud)',
            'POST /formats': 'Get all available formats (supports pCloud)',
            'POST /download': 'Download video (not supported for pCloud)',
            'POST /upload-cookies': 'Upload cookies file',
            'GET /cookies': 'List uploaded cookies files',
            'DELETE /cookies/<id>': 'Delete cookies file',
            'GET /pcloud-helper': 'Help for pCloud IP restrictions'
        },
        'supported_sources': [
            'All yt-dlp supported sites (YouTube, Vimeo, etc.)',
            'pCloud (u.pcloud.link/publink/show)'
        ],
        'cookie_options': {
            'cookies_file': 'Path to local cookies file',
            'cookies': 'Dictionary of cookies {name: value}',
            'cookies_content': 'Raw cookies file content',
            'headers': 'Custom HTTP headers'
        },
        'examples': {
            'pcloud_extract': {
                'url': 'POST /extract',
                'body': {
                    'url': 'https://u.pcloud.link/publink/show?code=XZ6v9u5ZkMmXtlVDhV4Veuz5zGduOj1DIVik',
                    'best_only': True
                }
            },
            'extract_with_cookies_dict': {
                'url': 'POST /extract',
                'body': {
                    'url': 'https://example.com/video',
                    'best_only': True,
                    'cookies': {
                        'session_id': 'abc123',
                        'auth_token': 'xyz789'
                    },
                    'headers': {
                        'User-Agent': 'Custom User Agent',
                        'Referer': 'https://example.com'
                    }
                }
            }
        }
    })

if __name__ == '__main__':
    # Puerto flexible para Railway
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port, threaded=True)