from flask import Flask, request, jsonify
from flask_cors import CORS
import yt_dlp
import json
import re
import requests
from urllib.parse import urlparse, parse_qs
import tempfile
import os
import time
import hashlib

app = Flask(__name__)
CORS(app)

class YTDLPExtractor:
    def __init__(self):
        self.base_ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'skip_download': True,
        }
        self.cookies_dir = os.path.join('/app', 'cookies')
        self.downloads_dir = os.path.join('/app', 'downloads')
        
        os.makedirs(self.cookies_dir, exist_ok=True)
        os.makedirs(self.downloads_dir, exist_ok=True)
    
    def is_pcloud_link(self, url):
        """Detecta si es un enlace de pCloud"""
        return "u.pcloud.link/publink/show" in url or "pcloud.link" in url
    
    def get_client_ip(self, request_obj):
        """Obtiene la IP del cliente que hace la petición"""
        # Intenta obtener la IP real considerando proxies/load balancers
        if request_obj.headers.getlist("X-Forwarded-For"):
            ip = request_obj.headers.getlist("X-Forwarded-For")[0].split(',')[0].strip()
        elif request_obj.headers.get('X-Real-IP'):
            ip = request_obj.headers.get('X-Real-IP')
        elif request_obj.headers.get('CF-Connecting-IP'):  # Cloudflare
            ip = request_obj.headers.get('CF-Connecting-IP')
        else:
            ip = request_obj.remote_addr
        return ip
    
    def extract_pcloud_m3u8_with_proxy(self, pcloud_url, client_ip=None, user_agent=None):
        """
        OPCIÓN 1: Usar la IP del cliente para hacer la petición
        Solo consume bandwidth para obtener el HTML (muy poco)
        """
        try:
            headers = {
                'User-Agent': user_agent or 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Referer': 'https://pcloud.com/',
            }
            
            # Si tenemos la IP del cliente, añadirla como X-Forwarded-For
            if client_ip:
                headers['X-Forwarded-For'] = client_ip
                headers['X-Real-IP'] = client_ip
            
            # Solo descargamos el HTML (pocos KB)
            response = requests.get(pcloud_url, headers=headers, timeout=15)
            response.raise_for_status()
            
            # Extraer JSON con mínimo procesamiento
            json_pattern = r'var publinkData = ({.*?});'
            json_match = re.search(json_pattern, response.text, re.DOTALL)
            
            if not json_match:
                # OPCIÓN 2: Intentar con diferentes patrones si falla
                alt_patterns = [
                    r'publinkData\s*=\s*({.*?});',
                    r'"publinkData":\s*({.*?})',
                    r'window\.publinkData\s*=\s*({.*?});'
                ]
                
                for pattern in alt_patterns:
                    match = re.search(pattern, response.text, re.DOTALL)
                    if match:
                        json_match = match
                        break
                
                if not json_match:
                    raise Exception("No se pudo extraer publinkData. Posible cambio en pCloud.")
            
            data = json.loads(json_match.group(1))
            
            # Procesar variantes HLS
            variants = data.get('variants', [])
            hls_formats = []
            
            for variant in variants:
                if variant.get('transcodetype') == 'hls':
                    path = variant['path']
                    hosts = variant.get('hosts', [])
                    
                    if hosts:
                        host = hosts[0]
                        m3u8_url = f"https://{host}{path}"
                        
                        # Validar que la URL sea accesible (HEAD request - mínimo bandwidth)
                        try:
                            head_response = requests.head(m3u8_url, timeout=5, headers={'User-Agent': headers['User-Agent']})
                            if head_response.status_code == 200:
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
                                    'expires': variant.get('expires'),
                                    'host': host,
                                    'source': 'pcloud',
                                    'verified': True
                                }
                                hls_formats.append(hls_format)
                        except:
                            # Si falla la verificación, incluir de todas formas
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
                                'expires': variant.get('expires'),
                                'host': host,
                                'source': 'pcloud',
                                'verified': False,
                                'warning': 'URL not verified - may require same IP'
                            }
                            hls_formats.append(hls_format)
            
            if not hls_formats:
                raise Exception("No se encontraron variantes HLS válidas")
            
            basic_info = {
                'title': data.get('name', 'pCloud Video'),
                'duration': data.get('duration'),
                'filesize': data.get('size'),
                'thumbnail': data.get('thumb1024', data.get('thumb', '')),
                'uploader': 'pCloud',
                'webpage_url': pcloud_url,
                'source': 'pcloud',
                'extracted_with_ip': client_ip,
                'expires_info': 'URLs may expire and require same IP as extraction'
            }
            
            return hls_formats, basic_info
            
        except requests.RequestException as e:
            raise Exception(f"Error de red al acceder a pCloud: {str(e)}")
        except json.JSONDecodeError as e:
            raise Exception(f"Error al parsear JSON de pCloud: {str(e)}")
        except Exception as e:
            raise Exception(f"Error procesando pCloud: {str(e)}")
    
    def extract_pcloud_direct_link_method(self, pcloud_url):
        """
        OPCIÓN 3: Método alternativo usando la API interna de pCloud
        Consume aún menos bandwidth
        """
        try:
            # Extraer code del URL
            if '?code=' in pcloud_url:
                code = pcloud_url.split('?code=')[1].split('&')[0]
            else:
                raise Exception("No se pudo extraer el código del enlace de pCloud")
            
            # Usar API interna de pCloud (menos bandwidth)
            api_url = f"https://api.pcloud.com/getpubzip"
            params = {
                'code': code,
                'forcedownload': 1,
                'skipfilename': 1
            }
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://pcloud.com/'
            }
            
            # Solo petición de metadatos (muy poco bandwidth)
            response = requests.get(api_url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('result') != 0:
                raise Exception(f"Error de API de pCloud: {data.get('error', 'Unknown error')}")
            
            # Extraer información de archivos
            files = data.get('metadata', {}).get('contents', [])
            video_files = [f for f in files if f.get('contenttype', '').startswith('video/')]
            
            if not video_files:
                raise Exception("No se encontraron archivos de video en el enlace de pCloud")
            
            # Tomar el primer archivo de video
            video_file = video_files[0]
            
            # Construir URL de descarga directa
            download_url = f"https://api.pcloud.com/getpublink?code={code}&linkid={video_file.get('id')}"
            
            return [{
                'format_id': 'pcloud_direct',
                'url': download_url,
                'ext': video_file.get('name', '').split('.')[-1] if '.' in video_file.get('name', '') else 'mp4',
                'filesize': video_file.get('size'),
                'format_note': 'pCloud Direct Download',
                'source': 'pcloud_api',
                'warning': 'This is a direct download URL, not HLS'
            }], {
                'title': video_file.get('name', 'pCloud Video'),
                'filesize': video_file.get('size'),
                'uploader': 'pCloud',
                'webpage_url': pcloud_url,
                'source': 'pcloud_api'
            }
            
        except Exception as e:
            raise Exception(f"Error con API de pCloud: {str(e)}")
    
    # ... resto de métodos sin cambios ...
    def save_cookies_file(self, cookies_content, filename):
        """Guarda cookies en un archivo temporal"""
        cookies_path = os.path.join(self.cookies_dir, filename)
        with open(cookies_path, 'w') as f:
            f.write(cookies_content)
        return cookies_path
    
    def prepare_ydl_opts(self, cookies_file=None, cookies_dict=None, headers=None):
        """Prepara opciones de yt-dlp con cookies y headers"""
        opts = self.base_ydl_opts.copy()
        
        if cookies_file and os.path.exists(cookies_file):
            opts['cookiefile'] = cookies_file
        
        if headers:
            opts['http_headers'] = headers
        
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
            line = f".example.com\tTRUE\t/\tFALSE\t0\t{name}\t{value}"
            lines.append(line)
        return '\n'.join(lines)
    
    def extract_info(self, url, extract_formats=True, cookies_file=None, cookies_dict=None, headers=None):
        """Extrae información del video usando yt-dlp o pCloud"""
        try:
            if self.is_pcloud_link(url):
                # Para pCloud, intentar ambos métodos
                try:
                    return self.extract_pcloud_m3u8_with_proxy(url)
                except:
                    # Fallback al método de API
                    return self.extract_pcloud_direct_link_method(url)
            
            opts = self.prepare_ydl_opts(cookies_file, cookies_dict, headers)
            if not extract_formats:
                opts['extract_flat'] = True
            
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info
                
        except Exception as e:
            raise Exception(f"Error extracting info: {str(e)}")
    
    def get_hls_urls(self, url, cookies_file=None, cookies_dict=None, headers=None, client_ip=None, user_agent=None):
        """Extrae URLs HLS específicamente"""
        try:
            if self.is_pcloud_link(url):
                # Método optimizado para pCloud con IP del cliente
                try:
                    hls_formats, basic_info = self.extract_pcloud_m3u8_with_proxy(url, client_ip, user_agent)
                    return hls_formats, basic_info
                except Exception as e:
                    # Fallback: intentar método de API
                    try:
                        direct_formats, basic_info = self.extract_pcloud_direct_link_method(url)
                        return direct_formats, basic_info
                    except:
                        raise e  # Re-lanzar el error original
            
            # Para otros sitios
            info = self.extract_info(url, cookies_file=cookies_file, cookies_dict=cookies_dict, headers=headers)
            hls_formats = []
            
            if 'formats' in info:
                for fmt in info['formats']:
                    if fmt.get('protocol') == 'm3u8' or fmt.get('protocol') == 'm3u8_native':
                        hls_formats.append({
                            'format_id': fmt.get('format_id'),
                            'url': fmt.get('url'),
                            'ext': fmt.get('ext'),
                            'quality': fmt.get('quality'),
                            'height': fmt.get('height'),
                            'width': fmt.get('width'),
                            'fps': fmt.get('fps'),
                            'tbr': fmt.get('tbr'),
                            'protocol': fmt.get('protocol'),
                            'format_note': fmt.get('format_note')
                        })
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
    
    def get_best_hls(self, url, cookies_file=None, cookies_dict=None, headers=None, client_ip=None, user_agent=None):
        """Obtiene la mejor calidad HLS disponible"""
        try:
            hls_formats, info = self.get_hls_urls(url, cookies_file, cookies_dict, headers, client_ip, user_agent)
            
            if not hls_formats:
                return None, info
            
            best_format = max(hls_formats, key=lambda x: (
                x.get('height', 0),
                x.get('tbr', 0)
            ))
            
            return best_format, info
        except Exception as e:
            raise Exception(f"Error getting best HLS: {str(e)}")

@app.route('/extract', methods=['POST'])
def extract_hls():
    """Extrae URLs HLS de un video (optimizado para pCloud con mínimo bandwidth)"""
    try:
        data = request.json
        url = data.get('url')
        best_only = data.get('best_only', False)
        
        # Opciones de autenticación
        cookies_file = data.get('cookies_file')
        cookies_dict = data.get('cookies')
        headers = data.get('headers')
        cookies_content = data.get('cookies_content')
        
        if not url:
            return jsonify({'error': 'URL is required'}), 400
        
        extractor = YTDLPExtractor()
        temp_cookies_file = None
        
        # Obtener IP del cliente y User-Agent
        client_ip = extractor.get_client_ip(request)
        user_agent = request.headers.get('User-Agent')
        
        try:
            if cookies_content:
                temp_file = f"uploaded_cookies_{int(time.time())}.txt"
                temp_cookies_file = extractor.save_cookies_file(cookies_content, temp_file)
                cookies_file = temp_cookies_file
            
            if best_only:
                best_format, info = extractor.get_best_hls(url, cookies_file, cookies_dict, headers, client_ip, user_agent)
                hls_formats = [best_format] if best_format else []
            else:
                hls_formats, info = extractor.get_hls_urls(url, cookies_file, cookies_dict, headers, client_ip, user_agent)
            
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
                'is_pcloud': is_pcloud,
                'client_ip': client_ip,
                'bandwidth_usage': 'minimal',  # Solo HTML/JSON, no contenido de video
                'notes': info.get('expires_info') if is_pcloud else None
            })
        
        finally:
            if temp_cookies_file and os.path.exists(temp_cookies_file):
                os.remove(temp_cookies_file)
    
    except Exception as e:
        return jsonify({
            'error': str(e),
            'suggestion': 'For pCloud links, the URL may require access from the same IP that generated the link'
        }), 500

# Nuevo endpoint específico para pCloud con opciones avanzadas
@app.route('/extract-pcloud', methods=['POST'])
def extract_pcloud_advanced():
    """Endpoint específico para pCloud con múltiples métodos de extracción"""
    try:
        data = request.json
        url = data.get('url')
        method = data.get('method', 'auto')  # 'auto', 'hls', 'direct'
        
        if not url:
            return jsonify({'error': 'URL is required'}), 400
        
        extractor = YTDLPExtractor()
        
        if not extractor.is_pcloud_link(url):
            return jsonify({'error': 'This endpoint is only for pCloud links'}), 400
        
        client_ip = extractor.get_client_ip(request)
        user_agent = request.headers.get('User-Agent')
        
        results = {}
        
        if method in ['auto', 'hls']:
            try:
                hls_formats, hls_info = extractor.extract_pcloud_m3u8_with_proxy(url, client_ip, user_agent)
                results['hls_method'] = {
                    'success': True,
                    'formats': hls_formats,
                    'info': hls_info
                }
            except Exception as e:
                results['hls_method'] = {
                    'success': False,
                    'error': str(e)
                }
        
        if method in ['auto', 'direct']:
            try:
                direct_formats, direct_info = extractor.extract_pcloud_direct_link_method(url)
                results['direct_method'] = {
                    'success': True,
                    'formats': direct_formats,
                    'info': direct_info
                }
            except Exception as e:
                results['direct_method'] = {
                    'success': False,
                    'error': str(e)
                }
        
        # Determinar el mejor resultado
        if results.get('hls_method', {}).get('success'):
            best_result = results['hls_method']
            used_method = 'hls'
        elif results.get('direct_method', {}).get('success'):
            best_result = results['direct_method']
            used_method = 'direct'
        else:
            return jsonify({
                'success': False,
                'error': 'All extraction methods failed',
                'details': results,
                'client_ip': client_ip
            }), 500
        
        return jsonify({
            'success': True,
            'url': url,
            'method_used': used_method,
            'title': best_result['info'].get('title'),
            'formats': best_result['formats'],
            'formats_count': len(best_result['formats']),
            'all_methods': results,
            'client_ip': client_ip,
            'bandwidth_usage': 'minimal'
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ... resto de endpoints sin cambios ...

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        'message': 'yt-dlp HLS Extractor API - Optimized for minimal bandwidth usage',
        'endpoints': {
            'POST /extract': 'Extract HLS URLs from video (optimized for pCloud)',
            'POST /extract-pcloud': 'pCloud-specific extraction with multiple methods',
            'POST /formats': 'Get all available formats',
            'POST /download': 'Download video (not recommended for bandwidth conservation)',
        },
        'pcloud_optimizations': {
            'ip_forwarding': 'Uses client IP to bypass IP restrictions',
            'minimal_bandwidth': 'Only downloads HTML/JSON metadata, not video content',
            'multiple_methods': 'HLS extraction and direct API methods',
            'verification': 'HEAD requests to verify URL accessibility'
        },
        'bandwidth_usage': {
            'extract': '< 50KB per request (only metadata)',
            'verify_urls': '< 1KB per URL (HEAD requests)',
            'no_video_download': 'Zero video content bandwidth usage'
        },
        'pcloud_tips': [
            'URLs work best when accessed from same IP that generated the link',
            'HLS URLs may expire after some time',
            'Direct download URLs bypass some IP restrictions',
            'Always use the client\'s IP for extraction when possible'
        ]
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port, threaded=True)