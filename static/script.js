class UziDatabaseApp {
    constructor() {
        this.currentCollection = 'current';
        this.currentPage = 1;
        this.perPage = 50;
        this.totalPages = 1;
        this.currentSearchQuery = '';
        this.autoUpdateInterval = null;
        
        this.initializeEventListeners();
        this.loadSongs(this.currentCollection, this.currentPage);
        this.updateStats();
        this.startAutoUpdateMonitoring();
    }
    
    initializeEventListeners() {
        // Tab buttons
        document.querySelectorAll('.tab-button').forEach(button => {
            button.addEventListener('click', (e) => {
                this.switchCollection(e.target.dataset.collection);
            });
        });
        
        // Auto-update controls
        document.getElementById('trigger-update').addEventListener('click', () => {
            this.triggerManualUpdate();
        });
        
        document.getElementById('refresh-status').addEventListener('click', () => {
            this.updateAutoUpdateStatus();
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
    
    startAutoUpdateMonitoring() {
        // Update auto-update status every 30 seconds
        this.updateAutoUpdateStatus();
        this.autoUpdateInterval = setInterval(() => {
            this.updateAutoUpdateStatus();
        }, 30000); // 30 seconds
    }
    
    async updateAutoUpdateStatus() {
        try {
            const response = await fetch('/api/auto-update/status');
            const status = await response.json();
            
            if (response.ok) {
                this.displayAutoUpdateStatus(status);
            } else {
                console.error('Error fetching auto-update status:', status.error);
            }
        } catch (error) {
            console.error('Error fetching auto-update status:', error);
        }
    }
    
    displayAutoUpdateStatus(status) {
        document.getElementById('auto-update-status').textContent = 
            status.enabled ? 'Active' : 'Disabled';
        document.getElementById('auto-update-interval').textContent = 
            `${status.interval_minutes} minutes`;
        document.getElementById('last-update').textContent = 
            status.last_update ? new Date(status.last_update).toLocaleString() : 'Never';
        document.getElementById('next-update').textContent = 
            status.next_update ? new Date(status.next_update).toLocaleString() : 'Calculating...';
        
        // Update status indicator
        const statusElement = document.getElementById('auto-update-status');
        if (status.in_progress) {
            statusElement.innerHTML = '🔄 Updating...';
            statusElement.style.color = '#ffa500'; // Orange
        } else if (status.enabled) {
            statusElement.innerHTML = '✅ Active';
            statusElement.style.color = '#4CAF50'; // Green
        } else {
            statusElement.innerHTML = '❌ Disabled';
            statusElement.style.color = '#F44336'; // Red
        }
    }
    
    async triggerManualUpdate() {
        const triggerBtn = document.getElementById('trigger-update');
        const originalText = triggerBtn.textContent;
        
        triggerBtn.disabled = true;
        triggerBtn.textContent = 'Triggering...';
        
        try {
            const response = await fetch('/api/auto-update/trigger', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });
            
            const result = await response.json();
            
            if (response.ok) {
                this.showNotification('Update triggered successfully!', 'success');
                // Refresh status after a short delay
                setTimeout(() => this.updateAutoUpdateStatus(), 2000);
            } else {
                throw new Error(result.error || 'Failed to trigger update');
            }
        } catch (error) {
            console.error('Error triggering update:', error);
            this.showNotification('Error: ' + error.message, 'error');
        } finally {
            triggerBtn.disabled = false;
            triggerBtn.textContent = originalText;
        }
    }
    
    showNotification(message, type) {
        // Create notification element
        const notification = document.createElement('div');
        notification.className = `notification ${type}`;
        notification.textContent = message;
        
        // Add styles
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 15px 20px;
            border-radius: 5px;
            color: white;
            font-weight: bold;
            z-index: 1000;
            opacity: 0;
            transition: opacity 0.3s;
        `;
        
        if (type === 'success') {
            notification.style.background = '#4CAF50';
        } else {
            notification.style.background = '#F44336';
        }
        
        document.body.appendChild(notification);
        
        // Animate in
        setTimeout(() => notification.style.opacity = '1', 100);
        
        // Remove after 3 seconds
        setTimeout(() => {
            notification.style.opacity = '0';
            setTimeout(() => notification.remove(), 300);
        }, 3000);
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
            
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || `HTTP ${response.status}: ${response.statusText}`);
            }
            
            const data = await response.json();
            
            if (data.error) {
                throw new Error(data.error);
            }
            
            this.displaySongs(data.songs);
            this.updatePagination(data);
            this.updateSearchInfo(data);
            
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
            tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; padding: 40px;">No songs found</td></tr>';
            return;
        }
        
        songs.forEach(song => {
            const row = document.createElement('tr');
            
            row.innerHTML = `
                <td>${song.track_number || ''}</td>
                <td>${this.escapeHtml(song.title)}</td>
                <td>${this.escapeHtml(song.artist)}</td>
                <td>${this.escapeHtml(song.album || '')}</td>
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
        tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: #ff6b6b; padding: 40px;">${message}</td></tr>`;
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