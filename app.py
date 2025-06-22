from flask import Flask, request, jsonify
from flask_cors import CORS
import yt_dlp
import json
import re
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
        """Extrae información del video usando yt-dlp"""
        try:
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
        """Lista sitios soportados por yt-dlp"""
        try:
            with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                extractors = ydl.list_extractors()
                return [extractor.IE_NAME for extractor in extractors if hasattr(extractor, 'IE_NAME')]
        except Exception as e:
            return []

@app.route('/extract', methods=['POST'])
def extract_hls():
    """Extrae URLs HLS de un video"""
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
                'used_headers': bool(headers)
            })
        
        finally:
            # Limpiar archivo temporal de cookies
            if temp_cookies_file and os.path.exists(temp_cookies_file):
                os.remove(temp_cookies_file)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/info', methods=['POST'])
def get_video_info():
    """Obtiene información completa del video"""
    try:
        data = request.json
        url = data.get('url')
        
        # Opciones de autenticación  
        cookies_file = data.get('cookies_file')
        cookies_dict = data.get('cookies')
        headers = data.get('headers')
        cookies_content = data.get('cookies_content')
        
        if not url:
            return jsonify({'error': 'URL is required'}), 400
        
        extractor = YTDLPExtractor()
        temp_cookies_file = None
        
        try:
            if cookies_content:
                temp_file = f"uploaded_cookies_{int(time.time())}.txt"
                temp_cookies_file = extractor.save_cookies_file(cookies_content, temp_file)
                cookies_file = temp_cookies_file
            
            info = extractor.extract_info(url, cookies_file=cookies_file, cookies_dict=cookies_dict, headers=headers)
        
            # Información básica
            basic_info = {
                'title': info.get('title'),
                'description': info.get('description'),
                'duration': info.get('duration'),
                'uploader': info.get('uploader'),
                'upload_date': info.get('upload_date'),
                'view_count': info.get('view_count'),
                'like_count': info.get('like_count'),
                'thumbnail': info.get('thumbnail'),
                'webpage_url': info.get('webpage_url'),
                'formats_count': len(info.get('formats', []))
            }
            
            # Contar formatos por protocolo
            protocols = {}
            if 'formats' in info:
                for fmt in info['formats']:
                    protocol = fmt.get('protocol', 'unknown')
                    protocols[protocol] = protocols.get(protocol, 0) + 1
            
            return jsonify({
                'success': True,
                'info': basic_info,
                'protocols_available': protocols,
                'used_cookies': bool(cookies_file or cookies_dict),
                'used_headers': bool(headers)
            })
        
        finally:
            if temp_cookies_file and os.path.exists(temp_cookies_file):
                os.remove(temp_cookies_file)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/formats', methods=['POST'])
def get_all_formats():
    """Obtiene todos los formatos disponibles"""
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
                    'language': fmt.get('language')
                }
                
                # Filtrar por protocolo si se especifica
                if filter_protocol:
                    if fmt.get('protocol') == filter_protocol:
                        formats.append(format_info)
                else:
                    formats.append(format_info)
        
        return jsonify({
            'success': True,
            'url': url,
            'title': info.get('title'),
            'formats_count': len(formats),
            'formats': formats
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download', methods=['POST'])
def download_video():
    """Descarga el video (opcional)"""
    try:
        data = request.json
        url = data.get('url')
        format_id = data.get('format_id', 'best')
        output_path = data.get('output_path', './downloads')
        
        if not url:
            return jsonify({'error': 'URL is required'}), 400
        
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

@app.route('/supported-sites', methods=['GET'])
def get_supported_sites():
    """Lista sitios soportados"""
    try:
        extractor = YTDLPExtractor()
        sites = extractor.get_supported_sites()
        
        return jsonify({
            'success': True,
            'supported_sites_count': len(sites),
            'supported_sites': sites[:50]  # Primeros 50 para no sobrecargar
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        'message': 'yt-dlp HLS Extractor API with Cookies Support',
        'endpoints': {
            'POST /extract': 'Extract HLS URLs from video',
            'POST /info': 'Get video information',
            'POST /formats': 'Get all available formats',
            'POST /download': 'Download video (optional)',
            'POST /upload-cookies': 'Upload cookies file',
            'GET /cookies': 'List uploaded cookies files',
            'DELETE /cookies/<id>': 'Delete cookies file',
            'GET /supported-sites': 'List supported sites'
        },
        'cookie_options': {
            'cookies_file': 'Path to local cookies file',
            'cookies': 'Dictionary of cookies {name: value}',
            'cookies_content': 'Raw cookies file content',
            'headers': 'Custom HTTP headers'
        },
        'examples': {
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
            },
            'extract_with_cookies_content': {
                'url': 'POST /extract',
                'body': {
                    'url': 'https://example.com/video',
                    'cookies_content': '# Netscape cookies file content here'
                }
            }
        }
    })

if __name__ == '__main__':
    # Puerto flexible para Railway
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port, threaded=True)