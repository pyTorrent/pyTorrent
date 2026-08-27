const staticImportVersion = encodeURIComponent(String(window.PYTORRENT?.staticHash || 'dev'));
const versionedImport = (path) => import(`${path}?v=${staticImportVersion}`);
const moduleImportSpecs = [
  ['./stateCore.js', 'stateCoreSource'],
  ['./columnState.js', 'columnStateSource'],
  ['./runtimeState.js', 'runtimeStateSource'],
  ['./sharedUi.js', 'sharedUiSource'],
  ['./torrentFilterHelpers.js', 'torrentFilterHelpersSource'],
  ['./torrentFilterUi.js', 'torrentFilterUiSource'],
  ['./torrentTrackerFilters.js', 'torrentTrackerFiltersSource'],
  ['./torrentTableState.js', 'torrentTableStateSource'],
  ['./torrentActionState.js', 'torrentActionStateSource'],
  ['./torrentRowRenderer.js', 'torrentRowRendererSource'],
  ['./torrentTableRenderer.js', 'torrentTableRendererSource'],
  ['./mobile.js', 'mobileSource'],
  ['./messages.js', 'messagesSource'],
  ['./torrentAdd.js', 'torrentAddSource'],
  ['./api.js', 'apiSource'],
  ['./createTorrent.js', 'createTorrentSource'],
  ['./torrentGeneralDetails.js', 'torrentGeneralDetailsSource'],
  ['./torrentFileDetails.js', 'torrentFileDetailsSource'],
  ['./torrentChunkDetails.js', 'torrentChunkDetailsSource'],
  ['./torrentPeerDetails.js', 'torrentPeerDetailsSource'],
  ['./torrentTrackerDetails.js', 'torrentTrackerDetailsSource'],
  ['./mobileTorrentDetails.js', 'mobileTorrentDetailsSource'],
  ['./torrentDetailsLoader.js', 'torrentDetailsLoaderSource'],
  ['./pathPickerTools.js', 'pathPickerToolsSource'],
  ['./columnManager.js', 'columnManagerSource'],
  ['./jobTools.js', 'jobToolsSource'],
  ['./labelTools.js', 'labelToolsSource'],
  ['./ratioTools.js', 'ratioToolsSource'],
  ['./rssTools.js', 'rssToolsSource'],
  ['./backupTools.js', 'backupToolsSource'],
  ['./smartQueue.js', 'smartQueueSource'],
  ['./rtorrentConfig.js', 'rtorrentConfigSource'],
  ['./appearancePreferences.js', 'appearancePreferencesSource'],
  ['./peerRefresh.js', 'peerRefreshSource'],
  ['./automationRules.js', 'automationRulesSource'],
  ['./cleanupTools.js', 'cleanupToolsSource'],
  ['./appDiagnostics.js', 'appDiagnosticsSource'],
  ['./connectionDiagnostics.js', 'connectionDiagnosticsSource'],
  ['./footerPreferences.js', 'footerPreferencesSource'],
  ['./liveSpeedStats.js', 'liveSpeedStatsSource'],
  ['./statusBar.js', 'statusBarSource'],
  ['./preferencesTools.js', 'preferencesToolsSource'],
  ['./profileCopy.js', 'profileCopySource'],
  ['./diskMonitor.js', 'diskMonitorSource'],
  ['./portCheckActions.js', 'portCheckActionsSource'],
  ['./appStatus.js', 'appStatusSource'],
  ['./torrentStats.js', 'torrentStatsSource'],
  ['./toolUiHelpers.js', 'toolUiHelpersSource'],
  ['./authUsers.js', 'authUsersSource'],
  ['./plannerToolsUi.js', 'plannerToolsUiSource'],
  ['./plannerSpeedControls.js', 'plannerSpeedControlsSource'],
  ['./plannerSettings.js', 'plannerSettingsSource'],
  ['./plannerPreviewHistory.js', 'plannerPreviewHistorySource'],
  ['./plannerActions.js', 'plannerActionsSource'],
  ['./searchHealth.js', 'searchHealthSource'],
  ['./notificationCenter.js', 'notificationCenterSource'],
  ['./diagnosticsDashboard.js', 'diagnosticsDashboardSource'],
  ['./dashboardTools.js', 'dashboardToolsSource'],
  ['./operationLogs.js', 'operationLogsSource'],
  ['./pollerSettings.js', 'pollerSettingsSource'],
  ['./toolsModal.js', 'toolsModalSource'],
  ['./toolPaneEvents.js', 'toolPaneEventsSource'],
  ['./rssEvents.js', 'rssEventsSource'],
  ['./smartQueueEvents.js', 'smartQueueEventsSource'],
  ['./backupCleanupRtconfigEvents.js', 'backupCleanupRtconfigEventsSource'],
  ['./automationEvents.js', 'automationEventsSource'],
  ['./labelSmartEvents.js', 'labelSmartEventsSource'],
  ['./torrentSelectionEvents.js', 'torrentSelectionEventsSource'],
  ['./torrentTableEvents.js', 'torrentTableEventsSource'],
  ['./preferenceEvents.js', 'preferenceEventsSource'],
  ['./keyboardEvents.js', 'keyboardEventsSource'],
  ['./speedLimitControls.js', 'speedLimitControlsSource'],
  ['./themeMobileControls.js', 'themeMobileControlsSource'],
  ['./jobSettings.js', 'jobSettingsSource'],
  ['./profileList.js', 'profileListSource'],
  ['./profileForm.js', 'profileFormSource'],
  ['./profileActions.js', 'profileActionsSource'],
  ['./profileSelection.js', 'profileSelectionSource'],
  ['./realtimeCharts.js', 'realtimeChartsSource'],
  ['./trafficHistoryData.js', 'trafficHistoryDataSource'],
  ['./trafficChartRenderer.js', 'trafficChartRendererSource'],
  ['./initialSnapshot.js', 'initialSnapshotSource'],
  ['./footerStatusRefresh.js', 'footerStatusRefreshSource'],
  ['./systemStatsSocket.js', 'systemStatsSocketSource'],
  ['./mobileSelectEvents.js', 'mobileSelectEventsSource'],
  ['./bootstrapRuntime.js', 'bootstrapRuntimeSource'],
];

export let moduleSources = [];
let moduleSourcesPromise = null;

async function loadModuleSources(){
  if(moduleSourcesPromise) return moduleSourcesPromise;
  // Note: Module imports report their real completion share so the loader no longer sits at one low percentage during the largest frontend bootstrap batch.
  let loadedModules = 0;
  const totalModules = Math.max(1, moduleImportSpecs.length);
  window.pyTorrentInitialLoaderProgress?.setStep('bootstrap', {percent: 4, label: 'Loading interface modules', auto: false});
  moduleSourcesPromise = Promise.all(moduleImportSpecs.map(async ([path]) => {
    const mod = await versionedImport(path);
    loadedModules += 1;
    const percent = 4 + (18 * loadedModules / totalModules);
    window.pyTorrentInitialLoaderProgress?.setStep('bootstrap', {percent, label: 'Loading interface modules', auto: false});
    return mod;
  })).then((modules) => {
    moduleSources = modules.map((mod, index) => mod[moduleImportSpecs[index][1]]);
    return moduleSources;
  });
  return moduleSourcesPromise;
}

function normalizeRuntimeSource(source){
  const text = String(source || '');
  // Note: Some generated source chunks may end with a literal \\n marker;
  // normalize only this trailing marker to avoid invalid Function() source.
  return text.endsWith('\\n') ? `${text.slice(0, -2)}\n` : text;
}

export async function buildRuntimeSource(){
  const sources = await loadModuleSources();
  return `(() => {\n${sources.map(normalizeRuntimeSource).join('\n')}\n})();\n`;
}

export async function startApp(){
  const runtimeSource = await buildRuntimeSource();
  // Note: Yield one paint after module loading so the interface-start milestone is visible before the synchronous shared runtime initializes.
  window.pyTorrentInitialLoaderProgress?.setStep('interface');
  await new Promise(resolve => {
    let settled = false;
    const finish = () => { if(!settled){ settled = true; resolve(); } };
    setTimeout(finish, 50);
    if(typeof requestAnimationFrame === 'function') requestAnimationFrame(finish);
    else finish();
  });
  // Keep the original shared lexical scope while loading the source from smaller ES modules.
  // `io` is passed explicitly so Socket.IO remains available inside the generated runtime.
  return Function('io', runtimeSource)(window.io);
}

if(typeof window !== 'undefined' && !window.PYTORRENT_DISABLE_AUTOSTART){
  // Note: A missing server bootstrap config is fatal; do not continue with defaults that can fake a missing profile or reset the loader.
  if(!window.PYTORRENT || typeof window.PYTORRENT !== 'object'){
    console.error('pyTorrent bootstrap configuration is missing');
    const loaderText = document.getElementById('initialLoaderText');
    if(loaderText) loaderText.textContent = 'Frontend bootstrap configuration is missing. Reload the page or clear browser cache.';
  }else{
    startApp().catch((error) => {
      console.error('pyTorrent frontend failed to start', error);
      const loaderText = document.getElementById('initialLoaderText');
      if(loaderText) loaderText.textContent = 'Frontend failed to start. Reload the page or clear browser cache.';
    });
  }
}
