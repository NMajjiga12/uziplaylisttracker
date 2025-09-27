from flask import Flask, render_template, jsonify, request
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from pymongo import UpdateOne
from sclib import SoundcloudAPI, Playlist
from datetime import datetime, timezone
import json
import threading
import re

app = Flask(__name__)

class SoundCloudMongoDBManager:
    def __init__(self, mongodb_uri):
        self.client = MongoClient(mongodb_uri, server_api=ServerApi('1'))
        self.db = self.client['soundcloud_playlists']
        self.collections = {
            'current': self.db['current_songs'],
            'all': self.db['all_songs'],
            'removed': self.db['removed_songs']
        }
        
        # Test connection
        try:
            self.client.admin.command('ping')
            print("✅ Successfully connected to MongoDB!")
        except Exception as e:
            print(f"❌ MongoDB connection error: {e}")
            raise

    def get_playlist_data(self, playlist_url):
        """Fetches current playlist data from SoundCloud."""
        try:
            api = SoundcloudAPI()
            playlist = api.resolve(playlist_url)
            
            current_songs = []
            for track in playlist.tracks:
                combined_title = f"{track.artist} - {track.title}"
                
                song_data = {
                    '_id': track.permalink_url,
                    'title': combined_title,
                    'artist': track.user['username'],
                    'duration_seconds': round(track.duration / 1000, 2),
                    'permalink_url': track.permalink_url,
                    'last_updated': datetime.now(timezone.utc),
                    'playlist_url': playlist_url,
                    'status': 'active'
                }
                current_songs.append(song_data)
            
            return current_songs
        except Exception as e:
            print(f"Error fetching playlist data: {e}")
            return []

    def update_database_threaded(self, playlist_url, callback=None):
        """Run database update in a separate thread to avoid blocking the web interface."""
        def update_task():
            try:
                result = self.update_database(playlist_url)
                if callback:
                    callback(result)
            except Exception as e:
                if callback:
                    callback({'error': str(e)})
        
        thread = threading.Thread(target=update_task)
        thread.daemon = True
        thread.start()

    def update_database(self, playlist_url):
        """Main function to update MongoDB with current playlist data."""
        current_songs = self.get_playlist_data(playlist_url)
        if not current_songs:
            return {'error': 'Failed to fetch playlist data'}
        
        current_songs_ids = {song['_id'] for song in current_songs}
        
        # Get existing songs from database
        existing_all_songs = list(self.collections['all'].find({}, {'_id': 1}))
        existing_all_ids = {song['_id'] for song in existing_all_songs}
        
        # Identify new songs
        new_songs_ids = current_songs_ids - existing_all_ids
        if new_songs_ids:
            new_songs = [song for song in current_songs if song['_id'] in new_songs_ids]
            self.collections['all'].insert_many(new_songs)
        
        # Update current songs collection
        self.collections['current'].delete_many({})
        if current_songs:
            self.collections['current'].insert_many(current_songs)
        
        # Identify removed songs
        removed_songs_ids = existing_all_ids - current_songs_ids
        if removed_songs_ids:
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
        
        # Update timestamps for current songs
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
        
        # Clean up removed_songs collection
        self.collections['removed'].delete_many({'_id': {'$in': list(current_songs_ids)}})
        
        return {
            'current_count': len(current_songs),
            'all_count': self.collections['all'].count_documents({}),
            'removed_count': self.collections['removed'].count_documents({}),
            'new_songs': len(new_songs_ids),
            'removed_songs': len(removed_songs_ids)
        }

    def get_songs(self, collection_type, page=1, per_page=50, search_query=None):
        """Get songs from specified collection with pagination and search."""
        collection = self.collections[collection_type]
        
        # Calculate skip value for pagination
        skip = (page - 1) * per_page
        
        # Build search filter
        search_filter = {}
        if search_query and search_query.strip():
            # Case-insensitive regex search on title and artist
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
            {'_id': 0, 'title': 1, 'artist': 1, 'duration_seconds': 1, 'last_updated': 1}
        ).sort('last_updated', -1).skip(skip).limit(per_page))
        
        # Convert datetime objects to strings for JSON serialization
        for song in songs:
            if 'last_updated' in song and isinstance(song['last_updated'], datetime):
                song['last_updated'] = song['last_updated'].strftime('%Y-%m-%d %H:%M:%S')
            if 'removed_date' in song and isinstance(song['removed_date'], datetime):
                song['removed_date'] = song['removed_date'].strftime('%Y-%m-%d %H:%M:%S')
        
        total = collection.count_documents(search_filter)
        
        return {
            'songs': songs,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page,
            'search_query': search_query
        }

    def get_collection_stats(self):
        """Get statistics for each collection."""
        stats = {}
        for name, collection in self.collections.items():
            stats[name] = collection.count_documents({})
        
        stats['all_active'] = self.collections['all'].count_documents({'status': 'active'})
        stats['all_removed'] = self.collections['all'].count_documents({'status': 'removed'})
        
        return stats

    def close_connection(self):
        """Close MongoDB connection."""
        self.client.close()

# Initialize MongoDB manager
uri = "mongodb+srv://nmajjiga_db_user:8B47aCTr1ROpe3uJ@uzidbcluster.ionbuld.mongodb.net/?retryWrites=true&w=majority&appName=UziDBCluster"
playlist_url = "https://soundcloud.com/luvshrimpie/sets/bufminl3qogq"

db_manager = SoundCloudMongoDBManager(uri)

# Global variable to track update status
update_status = {'in_progress': False, 'message': '', 'last_result': None}

@app.route('/')
def index():
    """Main page."""
    stats = db_manager.get_collection_stats()
    return render_template('index.html', stats=stats)

@app.route('/api/songs/<collection_type>')
def get_songs_api(collection_type):
    """API endpoint to get songs from a specific collection."""
    if collection_type not in ['current', 'all', 'removed']:
        return jsonify({'error': 'Invalid collection type'}), 400
    
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    search_query = request.args.get('search', '', type=str)
    
    try:
        data = db_manager.get_songs(collection_type, page, per_page, search_query)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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

@app.route('/api/update', methods=['POST'])
def update_database_api():
    """API endpoint to trigger database update."""
    global update_status
    
    if update_status['in_progress']:
        return jsonify({'error': 'Update already in progress'}), 429
    
    update_status['in_progress'] = True
    update_status['message'] = 'Update started...'
    
    def update_callback(result):
        global update_status
        update_status['in_progress'] = False
        if 'error' in result:
            update_status['message'] = f'Error: {result["error"]}'
        else:
            update_status['message'] = 'Update completed successfully!'
            update_status['last_result'] = result
    
    db_manager.update_database_threaded(playlist_url, update_callback)
    
    return jsonify({'message': 'Update started in background'})

@app.route('/api/update-status')
def get_update_status():
    """API endpoint to get current update status."""
    return jsonify(update_status)

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

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)