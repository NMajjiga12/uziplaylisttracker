class UziDatabaseApp {
    constructor() {
        this.currentCollection = 'current';
        this.currentPage = 1;
        this.perPage = 50;
        this.totalPages = 1;
        this.updateInterval = null;
        this.currentSearchQuery = '';
        
        this.initializeEventListeners();
        this.loadSongs(this.currentCollection, this.currentPage);
        this.updateStats();
    }
    
    initializeEventListeners() {
        // Tab buttons
        document.querySelectorAll('.tab-button').forEach(button => {
            button.addEventListener('click', (e) => {
                this.switchCollection(e.target.dataset.collection);
            });
        });
        
        // Update button
        document.getElementById('update-btn').addEventListener('click', () => {
            this.updateDatabase();
        });
        
        // Pagination buttons
        document.getElementById('prev-btn').addEventListener('click', () => {
            if (this.currentPage > 1) {
                this.currentPage--;
                this.loadSongs(this.currentCollection, this.currentPage, this.currentSearchQuery);
            }
        });
        
        document.getElementById('next-btn').addEventListener('click', () => {
            if (this.currentPage < this.totalPages) {
                this.currentPage++;
                this.loadSongs(this.currentCollection, this.currentPage, this.currentSearchQuery);
            }
        });
        
        // Search functionality
        document.getElementById('search-btn').addEventListener('click', () => {
            this.performSearch();
        });
        
        document.getElementById('clear-search').addEventListener('click', () => {
            this.clearSearch();
        });
        
        // Search on Enter key
        document.getElementById('search-input').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                this.performSearch();
            }
        });
    }
    
    switchCollection(collection) {
        this.currentCollection = collection;
        this.currentPage = 1;
        this.currentSearchQuery = '';
        
        // Update active tab
        document.querySelectorAll('.tab-button').forEach(button => {
            button.classList.remove('active');
        });
        document.querySelector(`[data-collection="${collection}"]`).classList.add('active');
        
        // Clear search input and info
        document.getElementById('search-input').value = '';
        document.getElementById('search-info').textContent = '';
        
        this.loadSongs(collection, this.currentPage);
    }
    
    async loadSongs(collection, page, searchQuery = '') {
        this.showLoading(true);
        
        try {
            let url = `/api/songs/${collection}?page=${page}&per_page=${this.perPage}`;
            if (searchQuery && searchQuery.trim()) {
                url += `&search=${encodeURIComponent(searchQuery.trim())}`;
            }
            
            const response = await fetch(url);
            const data = await response.json();
            
            if (response.ok) {
                this.displaySongs(data.songs);
                this.updatePagination(data);
                this.updateSearchInfo(data);
            } else {
                throw new Error(data.error || 'Failed to load songs');
            }
        } catch (error) {
            console.error('Error loading songs:', error);
            this.displayError('Failed to load songs: ' + error.message);
        } finally {
            this.showLoading(false);
        }
    }
    
    performSearch() {
        const searchQuery = document.getElementById('search-input').value.trim();
        this.currentSearchQuery = searchQuery;
        this.currentPage = 1;
        
        if (searchQuery) {
            this.loadSongs(this.currentCollection, this.currentPage, searchQuery);
        } else {
            this.clearSearch();
        }
    }
    
    clearSearch() {
        this.currentSearchQuery = '';
        this.currentPage = 1;
        document.getElementById('search-input').value = '';
        document.getElementById('search-info').textContent = '';
        this.loadSongs(this.currentCollection, this.currentPage);
    }
    
    displaySongs(songs) {
        const tbody = document.getElementById('songs-tbody');
        tbody.innerHTML = '';
        
        if (songs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; padding: 40px;">No songs found</td></tr>';
            return;
        }
        
        songs.forEach(song => {
            const row = document.createElement('tr');
            
            row.innerHTML = `
                <td>${this.escapeHtml(song.title)}</td>
                <td>${this.escapeHtml(song.artist)}</td>
                <td>${this.formatDuration(song.duration_seconds)}</td>
                <td>${song.last_updated || 'N/A'}</td>
            `;
            
            tbody.appendChild(row);
        });
    }
    
    updatePagination(data) {
        this.totalPages = data.total_pages;
        this.currentPage = data.page;
        
        document.getElementById('page-info').textContent = `Page ${this.currentPage} of ${this.totalPages}`;
        
        document.getElementById('prev-btn').disabled = this.currentPage <= 1;
        document.getElementById('next-btn').disabled = this.currentPage >= this.totalPages;
    }
    
    updateSearchInfo(data) {
        const searchInfo = document.getElementById('search-info');
        
        if (data.search_query && data.search_query.trim()) {
            if (data.total > 0) {
                searchInfo.textContent = `Found ${data.total} songs matching "${data.search_query}"`;
                searchInfo.style.color = '#4CAF50'; // Green for success
            } else {
                searchInfo.textContent = `No songs found matching "${data.search_query}"`;
                searchInfo.style.color = '#F44336'; // Red for no results
            }
        } else {
            searchInfo.textContent = '';
        }
    }
    
    async updateDatabase() {
        const updateBtn = document.getElementById('update-btn');
        const statusEl = document.getElementById('update-status');
        
        updateBtn.disabled = true;
        statusEl.textContent = 'Updating database...';
        statusEl.className = 'status-message info';
        
        try {
            const response = await fetch('/api/update', { method: 'POST' });
            const data = await response.json();
            
            if (response.ok) {
                statusEl.textContent = 'Update started. Please wait...';
                this.startUpdatePolling();
            } else {
                throw new Error(data.error || 'Failed to start update');
            }
        } catch (error) {
            console.error('Error starting update:', error);
            statusEl.textContent = 'Error: ' + error.message;
            statusEl.className = 'status-message error';
            updateBtn.disabled = false;
        }
    }
    
    startUpdatePolling() {
        if (this.updateInterval) {
            clearInterval(this.updateInterval);
        }
        
        this.updateInterval = setInterval(async () => {
            try {
                const response = await fetch('/api/update-status');
                const status = await response.json();
                
                const statusEl = document.getElementById('update-status');
                const updateBtn = document.getElementById('update-btn');
                
                if (!status.in_progress) {
                    clearInterval(this.updateInterval);
                    updateBtn.disabled = false;
                    
                    if (status.last_result) {
                        statusEl.textContent = status.message;
                        statusEl.className = 'status-message success';
                        
                        // Update stats and reload current view
                        this.updateStats();
                        this.loadSongs(this.currentCollection, this.currentPage, this.currentSearchQuery);
                    } else {
                        statusEl.textContent = status.message;
                        statusEl.className = 'status-message error';
                    }
                } else {
                    statusEl.textContent = status.message;
                }
            } catch (error) {
                console.error('Error polling update status:', error);
            }
        }, 2000);
    }
    
    async updateStats() {
        try {
            const response = await fetch('/api/stats');
            const stats = await response.json();
            
            document.getElementById('current-count').textContent = stats.current;
            document.getElementById('all-count').textContent = stats.all;
            document.getElementById('removed-count').textContent = stats.removed;
        } catch (error) {
            console.error('Error updating stats:', error);
        }
    }
    
    showLoading(show) {
        document.getElementById('loading').classList.toggle('hidden', !show);
        document.getElementById('songs-table').style.opacity = show ? '0.5' : '1';
    }
    
    displayError(message) {
        const tbody = document.getElementById('songs-tbody');
        tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: #ff6b6b; padding: 40px;">${message}</td></tr>`;
    }
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    formatDuration(seconds) {
        if (!seconds) return '0:00';
        const minutes = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${minutes}:${secs.toString().padStart(2, '0')}`;
    }
}

// Initialize the app when the page loads
document.addEventListener('DOMContentLoaded', () => {
    new UziDatabaseApp();
});
