from flask import Flask, render_template, jsonify, request
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from pymongo import UpdateOne
from sclib.asyncio import SoundcloudAPI, Track, Playlist
from datetime import datetime, timezone, timedelta
import json
import threading
import re
import logging
from logging.handlers import RotatingFileHandler
import sys
import traceback
import asyncio
import aiohttp
import time
import os

app = Flask(__name__)

# Configure logging
def setup_logging():
    """Setup comprehensive logging configuration"""
    import os
    if not os.path.exists('logs'):
        os.makedirs('logs')
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s [%(filename)s:%(lineno)d]'
    )
    
    file_handler = RotatingFileHandler(
        'logs/soundcloud_app.log', 
        maxBytes=10*1024*1024, 
        backupCount=5
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.DEBUG)
    
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

# Initialize logging
setup_logging()
logger = logging.getLogger(__name__)

class SoundCloudMongoDBManager:
    def __init__(self, mongodb_uri, playlist_url):
        self.logger = logging.getLogger(f"{__name__}.SoundCloudMongoDBManager")
        self.playlist_url = playlist_url
        self.auto_update_interval = 300  # 5 minutes
        self.is_auto_updating = False
        self.last_auto_update = None
        
        try:
            self.client = MongoClient(mongodb_uri, server_api=ServerApi('1'))
            self.db = self.client['soundcloud_playlists']
            self.collections = {
                'current': self.db['current_songs'],
                'all': self.db['all_songs'],
                'removed': self.db['removed_songs']
            }
            
            self.client.admin.command('ping')
            self.logger.info("Successfully connected to MongoDB!")
            
            # Start auto-update thread
            self.start_auto_updates()
            
        except Exception as e:
            self.logger.error(f"MongoDB connection error: {e}")
            self.logger.error(traceback.format_exc())
            raise

    def start_auto_updates(self):
        """Start the automatic update thread"""
        def auto_update_loop():
            while True:
                try:
                    time.sleep(self.auto_update_interval)
                    if not self.is_auto_updating:
                        self.logger.info("Starting automatic playlist update")
                        self.update_database_threaded(self.playlist_url, self.auto_update_callback)
                except Exception as e:
                    self.logger.error(f"Error in auto-update loop: {e}")
        
        auto_update_thread = threading.Thread(target=auto_update_loop)
        auto_update_thread.daemon = True
        auto_update_thread.start()
        self.logger.info("Auto-update thread started")

    def auto_update_callback(self, result):
        """Callback for automatic updates"""
        self.is_auto_updating = False
        self.last_auto_update = datetime.now(timezone.utc)
        
        if 'error' in result:
            self.logger.error(f"Auto-update failed: {result['error']}")
        else:
            self.logger.info(f"Auto-update completed: {result.get('new_songs', 0)} new songs, {result.get('removed_songs', 0)} removed")

    async def get_playlist_data_async(self, playlist_url):
        """Asynchronously fetch current playlist data from SoundCloud."""
        try:
            self.logger.info(f"Async: Fetching playlist data from: {playlist_url}")
            
            api = SoundcloudAPI()
            playlist = await api.resolve(playlist_url)
            
            if not playlist or not hasattr(playlist, 'tracks'):
                self.logger.error("No tracks found in playlist or invalid playlist response")
                return []
            
            current_songs = []
            for track_number, track in enumerate(playlist.tracks, 1):
                try:
                    # Set track number and album info
                    track.track_no = track_number
                    if hasattr(playlist, 'title'):
                        track.album = playlist.title
                    
                    artist = track.user['username'] if track.user else 'Unknown Artist'
                    combined_title = f"{artist} - {track.title}"
                    
                    song_data = {
                        '_id': track.permalink_url,
                        'title': combined_title,
                        'artist': artist,
                        'duration_seconds': round(track.duration / 1000, 2) if track.duration else 0,
                        'permalink_url': track.permalink_url,
                        'last_updated': datetime.now(timezone.utc),
                        'playlist_url': playlist_url,
                        'status': 'active',
                        'track_number': track_number,
                        'album': playlist.title if hasattr(playlist, 'title') else 'Unknown Album'
                    }
                    current_songs.append(song_data)
                    
                except Exception as track_error:
                    self.logger.warning(f"Error processing track {track_number}: {track_error}")
                    continue
            
            self.logger.info(f"Async: Successfully processed {len(current_songs)} tracks from playlist")
            return current_songs
            
        except Exception as e:
            self.logger.error(f"Async: Error fetching playlist data: {e}")
            self.logger.error(traceback.format_exc())
            return []

    def get_playlist_data(self, playlist_url):
        """Synchronous wrapper for async playlist data fetching."""
        try:
            # Run async function in a new event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(self.get_playlist_data_async(playlist_url))
            loop.close()
            return result
        except Exception as e:
            self.logger.error(f"Error in sync wrapper: {e}")
            return []

    async def download_track_async(self, track, download_dir="./downloads"):
        """Asynchronously download a track as MP3."""
        import os
        try:
            if not os.path.exists(download_dir):
                os.makedirs(download_dir)
            
            # Create safe filename
            safe_artist = "".join(c for c in track.artist if c.isalnum() or c in (' ', '-', '_')).rstrip()
            safe_title = "".join(c for c in track.title if c.isalnum() or c in (' ', '-', '_')).rstrip()
            filename = f"{safe_artist} - {safe_title}.mp3"
            filepath = os.path.join(download_dir, filename)
            
            # Check if file already exists
            if os.path.exists(filepath):
                self.logger.info(f"File already exists: {filename}")
                return filepath
            
            self.logger.info(f"Downloading: {filename}")
            
            async with aiohttp.ClientSession() as session:
                # Download the track
                with open(filepath, 'wb+') as file:
                    await track.write_mp3_to(file)
            
            self.logger.info(f"Downloaded: {filename}")
            return filepath
            
        except Exception as e:
            self.logger.error(f"Error downloading track {track.title}: {e}")
            return None

    async def download_playlist_async(self, playlist_url, download_dir="./downloads"):
        """Asynchronously download all tracks from a playlist."""
        try:
            api = SoundcloudAPI()
            playlist = await api.resolve(playlist_url)
            
            if not playlist or not hasattr(playlist, 'tracks'):
                self.logger.error("No tracks found in playlist")
                return []
            
            downloaded_files = []
            
            # Download tracks concurrently
            download_tasks = []
            for track_number, track in enumerate(playlist.tracks, 1):
                # Set metadata
                track.track_no = track_number
                if hasattr(playlist, 'title'):
                    track.album = playlist.title
                
                download_tasks.append(self.download_track_async(track, download_dir))
            
            # Wait for all downloads to complete
            downloaded_files = await asyncio.gather(*download_tasks, return_exceptions=True)
            
            # Filter out exceptions and None values
            successful_downloads = [f for f in downloaded_files if f and not isinstance(f, Exception)]
            
            self.logger.info(f"Downloaded {len(successful_downloads)} tracks from playlist")
            return successful_downloads
            
        except Exception as e:
            self.logger.error(f"Error downloading playlist: {e}")
            return []

    def update_database_threaded(self, playlist_url, callback=None):
        """Run database update in a separate thread with async support."""
        def update_task():
            try:
                self.logger.info("Starting database update in background thread")
                result = self.update_database(playlist_url)
                if callback:
                    callback(result)
                self.logger.info("Database update completed successfully")
            except Exception as e:
                self.logger.error(f"Error in update thread: {e}")
                self.logger.error(traceback.format_exc())
                if callback:
                    callback({'error': str(e), 'traceback': traceback.format_exc()})
        
        thread = threading.Thread(target=update_task)
        thread.daemon = True
        thread.start()

    def update_database(self, playlist_url):
        """Main function to update MongoDB with current playlist data."""
        try:
            self.logger.info("Starting database update process")
            
            current_songs = self.get_playlist_data(playlist_url)
            if not current_songs:
                error_msg = 'Failed to fetch playlist data or no songs found'
                self.logger.error(error_msg)
                return {'error': error_msg}
            
            current_songs_ids = {song['_id'] for song in current_songs}
            self.logger.info(f"Processing {len(current_songs_ids)} current songs")
            
            # Get existing songs from database
            try:
                existing_all_songs = list(self.collections['all'].find({}, {'_id': 1}))
                existing_all_ids = {song['_id'] for song in existing_all_songs}
                self.logger.info(f"Found {len(existing_all_ids)} existing songs in database")
            except Exception as e:
                self.logger.error(f"Error fetching existing songs: {e}")
                return {'error': f'Database error: {str(e)}'}
            
            # Identify new songs
            new_songs_ids = current_songs_ids - existing_all_ids
            new_songs = []
            if new_songs_ids:
                try:
                    new_songs = [song for song in current_songs if song['_id'] in new_songs_ids]
                    if new_songs:
                        self.collections['all'].insert_many(new_songs)
                        self.logger.info(f"Added {len(new_songs)} new songs to database")
                        
                        # Start async download of new songs
                        self.download_new_songs_async(new_songs)
                except Exception as e:
                    self.logger.error(f"Error inserting new songs: {e}")
            
            # Update current songs collection
            try:
                self.collections['current'].delete_many({})
                if current_songs:
                    self.collections['current'].insert_many(current_songs)
                self.logger.info("Updated current songs collection")
            except Exception as e:
                self.logger.error(f"Error updating current songs: {e}")
                return {'error': f'Error updating current songs: {str(e)}'}
            
            # Identify removed songs
            removed_songs_ids = existing_all_ids - current_songs_ids
            if removed_songs_ids:
                try:
                    removed_songs_detailed = list(self.collections['all'].find(
                        {'_id': {'$in': list(removed_songs_ids)}}
                    ))
                    
                    current_time = datetime.now(timezone.utc)
                    for song in removed_songs_detailed:
                        self.collections['all'].update_one(
                            {'_id': song['_id']},
                            {
                                '$set': {
                                    'status': 'removed',
                                    'removed_date': current_time,
                                    'last_updated': current_time
                                }
                            }
                        )
                        
                        song['removed_date'] = current_time
                        song['status'] = 'removed'
                        self.collections['removed'].replace_one(
                            {'_id': song['_id']}, song, upsert=True
                        )
                    
                    self.logger.info(f"Marked {len(removed_songs_ids)} songs as removed")
                except Exception as e:
                    self.logger.error(f"Error processing removed songs: {e}")
            
            # Update timestamps for current songs
            try:
                update_operations = []
                current_time = datetime.now(timezone.utc)
                
                for song_id in current_songs_ids:
                    update_operations.append(
                        UpdateOne(
                            {'_id': song_id},
                            {
                                '$set': {'last_updated': current_time, 'status': 'active'},
                                '$unset': {'removed_date': ""}
                            }
                        )
                    )
                
                if update_operations:
                    self.collections['all'].bulk_write(update_operations)
                    self.logger.info(f"Updated timestamps for {len(update_operations)} songs")
            except Exception as e:
                self.logger.error(f"Error updating timestamps: {e}")
            
            # Clean up removed_songs collection
            try:
                self.collections['removed'].delete_many({'_id': {'$in': list(current_songs_ids)}})
            except Exception as e:
                self.logger.error(f"Error cleaning up removed songs: {e}")
            
            result = {
                'current_count': len(current_songs),
                'all_count': self.collections['all'].count_documents({}),
                'removed_count': self.collections['removed'].count_documents({}),
                'new_songs': len(new_songs_ids),
                'removed_songs': len(removed_songs_ids),
                'success': True,
                'auto_update': True
            }
            
            self.logger.info(f"Update completed: {result}")
            return result
            
        except Exception as e:
            self.logger.error(f"Critical error in update_database: {e}")
            self.logger.error(traceback.format_exc())
            return {'error': str(e), 'success': False}

    def download_new_songs_async(self, new_songs):
        """Start async download of new songs."""
        def download_task():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                async def download_songs():
                    api = SoundcloudAPI()
                    downloaded_files = []
                    
                    for song in new_songs:
                        try:
                            track = await api.resolve(song['_id'])  # permalink_url is the track URL
                            if track:
                                filepath = await self.download_track_async(track)
                                if filepath:
                                    downloaded_files.append(filepath)
                        except Exception as e:
                            self.logger.error(f"Error downloading {song['title']}: {e}")
                            continue
                    
                    return downloaded_files
                
                downloaded = loop.run_until_complete(download_songs())
                loop.close()
                
                self.logger.info(f"Downloaded {len(downloaded)} new songs")
                
            except Exception as e:
                self.logger.error(f"Error in download task: {e}")
        
        download_thread = threading.Thread(target=download_task)
        download_thread.daemon = True
        download_thread.start()

    def get_songs(self, collection_type, page=1, per_page=50, search_query=None):
        """Get songs from specified collection with enhanced error handling."""
        try:
            if collection_type not in self.collections:
                self.logger.error(f"Invalid collection type: {collection_type}")
                return {'songs': [], 'total': 0, 'page': page, 'per_page': per_page, 'total_pages': 0}
            
            collection = self.collections[collection_type]
            skip = (page - 1) * per_page
            
            # Build search filter
            search_filter = {}
            if search_query and search_query.strip():
                regex_pattern = f'.*{re.escape(search_query)}.*'
                search_filter = {
                    '$or': [
                        {'title': {'$regex': regex_pattern, '$options': 'i'}},
                        {'artist': {'$regex': regex_pattern, '$options': 'i'}}
                    ]
                }
            
            # Get songs with pagination and search
            songs = list(collection.find(
                search_filter, 
                {'_id': 0, 'title': 1, 'artist': 1, 'duration_seconds': 1, 'last_updated': 1, 'track_number': 1, 'album': 1}
            ).sort('last_updated', -1).skip(skip).limit(per_page))
            
            # Convert datetime objects to strings for JSON serialization
            for song in songs:
                if 'last_updated' in song and isinstance(song['last_updated'], datetime):
                    song['last_updated'] = song['last_updated'].strftime('%Y-%m-%d %H:%M:%S')
                if 'removed_date' in song and isinstance(song['removed_date'], datetime):
                    song['removed_date'] = song['removed_date'].strftime('%Y-%m-%d %H:%M:%S')
            
            total = collection.count_documents(search_filter)
            
            self.logger.debug(f"Retrieved {len(songs)} songs from {collection_type} (page {page})")
            
            return {
                'songs': songs,
                'total': total,
                'page': page,
                'per_page': per_page,
                'total_pages': (total + per_page - 1) // per_page,
                'search_query': search_query
            }
            
        except Exception as e:
            self.logger.error(f"Error in get_songs for {collection_type}: {e}")
            self.logger.error(traceback.format_exc())
            return {'songs': [], 'total': 0, 'page': page, 'per_page': per_page, 'total_pages': 0}

    def get_collection_stats(self):
        """Get statistics for each collection with error handling."""
        try:
            stats = {}
            for name, collection in self.collections.items():
                stats[name] = collection.count_documents({})
            
            stats['all_active'] = self.collections['all'].count_documents({'status': 'active'})
            stats['all_removed'] = self.collections['all'].count_documents({'status': 'removed'})
            
            return stats
        except Exception as e:
            self.logger.error(f"Error getting collection stats: {e}")
            return {'current': 0, 'all': 0, 'removed': 0, 'all_active': 0, 'all_removed': 0}

    def close_connection(self):
        """Close MongoDB connection."""
        self.client.close()

# Initialize MongoDB manager
uri = "mongodb+srv://nmajjiga_db_user:8B47aCTr1ROpe3uJ@uzidbcluster.ionbuld.mongodb.net/?retryWrites=true&w=majority&appName=UziDBCluster"
playlist_url = "https://soundcloud.com/luvshrimpie/sets/bufminl3qogq"

db_manager = SoundCloudMongoDBManager(uri, playlist_url)

# Global variable to track auto-update status
auto_update_status = {
    'enabled': True,
    'interval_minutes': 5,
    'last_update': None,
    'next_update': None,
    'in_progress': False,
    'message': 'Auto-update system initialized'
}

@app.route('/')
def index():
    """Main page."""
    stats = db_manager.get_collection_stats()
    auto_update_status['last_update'] = db_manager.last_auto_update
    auto_update_status['in_progress'] = db_manager.is_auto_updating
    if db_manager.last_auto_update:
        next_update = db_manager.last_auto_update + timedelta(seconds=db_manager.auto_update_interval)
        auto_update_status['next_update'] = next_update
    return render_template('index.html', stats=stats, auto_update_status=auto_update_status)

@app.route('/api/auto-update/status')
def get_auto_update_status():
    """API endpoint to get auto-update status."""
    # Calculate next update time
    if db_manager.last_auto_update:
        next_update = db_manager.last_auto_update + timedelta(seconds=db_manager.auto_update_interval)
        auto_update_status['next_update'] = next_update.isoformat()
    
    auto_update_status['last_update'] = db_manager.last_auto_update.isoformat() if db_manager.last_auto_update else None
    auto_update_status['in_progress'] = db_manager.is_auto_updating
    
    return jsonify(auto_update_status)

@app.route('/api/auto-update/trigger', methods=['POST'])
def trigger_auto_update():
    """Manually trigger an auto-update."""
    if db_manager.is_auto_updating:
        return jsonify({'error': 'Update already in progress'}), 429
    
    db_manager.update_database_threaded(playlist_url, db_manager.auto_update_callback)
    return jsonify({'message': 'Manual update triggered successfully'})

@app.route('/api/auto-update/settings', methods=['POST'])
def update_auto_update_settings():
    """Update auto-update settings."""
    data = request.get_json()
    
    if 'interval_minutes' in data:
        new_interval = data['interval_minutes']
        if new_interval >= 1:  # Minimum 1 minute
            db_manager.auto_update_interval = new_interval * 60  # Convert to seconds
            auto_update_status['interval_minutes'] = new_interval
    
    if 'enabled' in data:
        auto_update_status['enabled'] = data['enabled']
        # Note: Actual enabling/disabling would require more complex logic
    
    return jsonify({'message': 'Settings updated successfully', 'settings': auto_update_status})

@app.route('/api/songs/<collection_type>')
def get_songs_api(collection_type):
    """API endpoint to get songs from a specific collection."""
    try:
        if collection_type not in ['current', 'all', 'removed']:
            logger.warning(f"Invalid collection type requested: {collection_type}")
            return jsonify({'error': 'Invalid collection type'}), 400
        
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        search_query = request.args.get('search', '', type=str)
        
        # Validate parameters
        if page < 1 or per_page < 1 or per_page > 100:
            return jsonify({'error': 'Invalid pagination parameters'}), 400
        
        data = db_manager.get_songs(collection_type, page, per_page, search_query)
        return jsonify(data)
        
    except Exception as e:
        logger.error(f"Error in get_songs_api: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/search/<collection_type>')
def search_songs_api(collection_type):
    """API endpoint specifically for search functionality."""
    if collection_type not in ['current', 'all', 'removed']:
        return jsonify({'error': 'Invalid collection type'}), 400
    
    search_query = request.args.get('q', '', type=str)
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    
    try:
        data = db_manager.get_songs(collection_type, page, per_page, search_query)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats')
def get_stats():
    """API endpoint to get collection statistics."""
    stats = db_manager.get_collection_stats()
    return jsonify(stats)

@app.template_filter('format_duration')
def format_duration_filter(seconds):
    """Format duration in seconds to MM:SS format."""
    if not seconds:
        return "0:00"
    minutes = int(seconds // 60)
    seconds = int(seconds % 60)
    return f"{minutes}:{seconds:02d}"

@app.template_filter('format_datetime')
def format_datetime_filter(dt):
    """Format datetime to readable string."""
    if not dt:
        return "Never"
    if isinstance(dt, str):
        return dt
    return dt.strftime('%Y-%m-%d %H:%M:%S')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)