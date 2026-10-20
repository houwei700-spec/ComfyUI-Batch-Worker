/**
 * Batch Hub State (全局响应式状态)
 */
const AppState = {
    servers: [],
    activeServerId: '',
    tasks: [],
    templates: [],
    currentSchema: null,
    assets: { models: [], apparels: [], scenes: [] },
    batchActive: false,
    workerStatuses: {},
    currentPlayingVideo: null,
    pollTimer: null
};
