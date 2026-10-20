/**
 * Batch Hub API Client (数据请求层)
 */
const API = {
    async getStatus() {
        const res = await fetch('/api/status');
        return await res.json();
    },
    async getConfig() {
        const res = await fetch('/api/config');
        return await res.json();
    },
    async saveConfig(cfg) {
        const res = await fetch('/api/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(cfg)
        });
        return await res.json();
    },
    async getTasks() {
        const res = await fetch('/api/tasks');
        return await res.json();
    },
    async addTask(task) {
        const res = await fetch('/api/tasks/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(task)
        });
        return await res.json();
    },
    async deleteTask(idx) {
        const res = await fetch('/api/tasks/delete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ index: idx })
        });
        return await res.json();
    },
    async retryFailed() {
        const res = await fetch('/api/tasks/retry_failed', { method: 'POST' });
        return await res.json();
    },
    async startBatch() {
        const res = await fetch('/api/batch/start', { method: 'POST' });
        return await res.json();
    },
    async stopBatch() {
        const res = await fetch('/api/batch/stop', { method: 'POST' });
        return await res.json();
    },
    async getTemplates() {
        const res = await fetch('/api/templates');
        return await res.json();
    },
    async getSchema(templatePath) {
        const res = await fetch('/api/schema?template=' + encodeURIComponent(templatePath));
        return await res.json();
    },
    async getAssets() {
        const res = await fetch('/api/assets');
        return await res.json();
    },
    async probeServer(url) {
        const res = await fetch('/api/probe_server', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ url })
        });
        return await res.json();
    },
    async openFolder() {
        const res = await fetch('/api/open_folder', { method: 'POST' });
        return await res.json();
    },
    async locateFile(filename) {
        const res = await fetch('/api/locate_file', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ filename })
        });
        return await res.json();
    }
};
