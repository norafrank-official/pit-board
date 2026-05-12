class F1CommandCenter {
    constructor() {
        const serverUrl = window.location.protocol === 'file:'
            ? 'http://localhost:5000'
            : window.location.origin;
        this.socket = io(serverUrl);
        this.map = null;
        this.circuitMarker = null;
        this.nextRaceDate = null;
        this.trackUtcOffset = 0;
        this.lastTimingData = {};
        this.prevPositions = {};
        this.isOffWeekend = false;
        this.init();
    }

    init() {
        this.setupSocket();
        this.initMap();
        this.startClock();
        this.setupTabs();
    }

    // ===== Real-time WebSocket Feed =====
    setupSocket() {
        this.socket.on('connect', () => {
            document.getElementById('status-signal').textContent = 'CONNECTED';
            document.getElementById('status-signal').style.color = '#00cc44';
        });
        this.socket.on('disconnect', () => {
            document.getElementById('status-signal').textContent = 'DISCONNECTED';
            document.getElementById('status-signal').style.color = '#e10600';
        });
        this.socket.on('data_update', (data) => this.updateUI(data));
        this.socket.on('connected', () => {});
    }

    // ===== Tab Navigation =====
    setupTabs() {
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                const tab = btn.dataset.tab;
                document.getElementById('standings-drivers').classList.toggle('hidden', tab !== 'drivers');
                document.getElementById('standings-constructors').classList.toggle('hidden', tab !== 'constructors');
                document.getElementById('standings-calendar').classList.toggle('hidden', tab !== 'calendar');
            });
        });
    }

    // ======================================================
    // CLOCK + COUNTDOWN
    // ======================================================
    startClock() {
        const tick = () => {
            const now = new Date();
            const utcMs = now.getTime() + (now.getTimezoneOffset() * 60000);
            const trackTime = new Date(utcMs + (this.trackUtcOffset * 3600000));
            document.getElementById('clock-local').textContent =
                trackTime.toLocaleTimeString('en-GB', { hour12: false });

            const istTime = new Date(utcMs + (5.5 * 3600000));
            document.getElementById('clock-ist').textContent =
                istTime.toLocaleTimeString('en-GB', { hour12: false });

            if (this.nextRaceDate) {
                const diff = this.nextRaceDate - now;
                if (diff > 0) {
                    const d = Math.floor(diff / 86400000);
                    const h = Math.floor((diff % 86400000) / 3600000);
                    const m = Math.floor((diff % 3600000) / 60000);
                    const s = Math.floor((diff % 60000) / 1000);
                    document.getElementById('cd-days').textContent = String(d).padStart(2, '0');
                    document.getElementById('cd-hours').textContent = String(h).padStart(2, '0');
                    document.getElementById('cd-mins').textContent = String(m).padStart(2, '0');
                    document.getElementById('cd-secs').textContent = String(s).padStart(2, '0');
                } else {
                    ['cd-days','cd-hours','cd-mins','cd-secs'].forEach(id =>
                        document.getElementById(id).textContent = '00');
                }
            }
        };
        tick();
        setInterval(tick, 1000);
    }

    // ======================================================
    // MAP
    // ======================================================
    initMap() {
        this.map = L.map('track-map', { zoomControl: false, attributionControl: false })
            .setView([43.7347, 7.4206], 13);
        L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', { maxZoom: 18 })
            .addTo(this.map);
        this.circuitMarker = L.marker([43.7347, 7.4206], {
            icon: L.divIcon({
                className: 'circuit-marker',
                html: '<div class="map-pin">🏁</div>',
                iconSize: [28, 28], iconAnchor: [14, 14]
            })
        }).addTo(this.map);
    }

    updateMap(race, coords) {
        if (!coords || !coords.lat || !coords.lon) return;
        this.map.setView([coords.lat, coords.lon], 14);
        this.circuitMarker.setLatLng([coords.lat, coords.lon]);
        this.circuitMarker.bindPopup(
            `<b>${race.flag || '🏁'} ${race.name || 'Circuit'}</b><br>
             ${race.location || ''}, ${race.country || ''}<br>
             <span style="color:#e10600">${race.session_name || ''}</span>`
        );
    }

    // ======================================================
    // MAIN UI UPDATE + MODE SWITCH
    // ======================================================
    updateUI(data) {
        if (data.track_utc_offset !== undefined) this.trackUtcOffset = data.track_utc_offset;

        if (data.current_race) {
            this.updateSessionBadge(data.current_race);
            this.updateCurrentRace(data.current_race);
        }
        this.updateNextRace(data.next_race);
        if (data.standings) this.updateStandings(data.standings);
        this.updateWeather(data.track_weather, data.external_weather);
        if (data.race_control !== undefined) {
            this.updateRaceControl(data.race_control);
            this.updateAlertBanner(data.race_control);
        }
        if (data.team_radio !== undefined) this.updateRadio(data.team_radio);
        if (data.weekend_schedule) this.updateWeekendSchedule(data.weekend_schedule);
        if (data.race_calendar) this.updateCalendar(data.race_calendar);
        if (data.current_race && data.circuit_coords) {
            this.updateMap(data.current_race, data.circuit_coords);
        }
        if (data.forecast !== undefined) {
            this.updateForecast(data.forecast, data.next_race || data.current_race);
        }

        // Determine display mode
        const isOff = !!data.is_off_weekend;
        this.setMode(isOff, data);
    }

    // ======================================================
    // MODE SWITCH
    // ======================================================
    setMode(isOff, data) {
        if (this.isOffWeekend === isOff && this._modeInitialized) {
            // Same mode — just refresh content
            if (isOff) {
                this.updateOffWeekend(data);
            } else {
                if (data.live_timing) this.updateTiming(data.live_timing, data.circuit_info, data.current_race?.session_name);
                this.updateLiveBottom(data);
            }
            return;
        }

        this.isOffWeekend = isOff;
        this._modeInitialized = true;

        // Switch center panel
        document.getElementById('live-view').classList.toggle('hidden', isOff);
        document.getElementById('offweek-view').classList.toggle('hidden', !isOff);

        // Switch bottom sections 1 & 2
        const sec1Title = document.getElementById('sec1-title');
        const sec2Title = document.getElementById('sec2-title');
        const histTag = document.getElementById('history-circuit-tag');
        const drsBadge = document.getElementById('drs-badge');
        const rcBadge = document.getElementById('rc-live-badge');
        const radioBadge = document.getElementById('radio-live-badge');

        if (isOff) {
            sec1Title.textContent = 'TEAMMATE BATTLES';
            sec2Title.textContent = 'PREVIOUS WINNERS';
            drsBadge.classList.add('hidden');
            histTag.classList.remove('hidden');
            rcBadge.textContent = 'LAST RACE';
            radioBadge.textContent = '—';
            document.getElementById('sec1-content').className = 'teammate-list';
            document.getElementById('sec2-content').className = 'history-list';
            this.updateOffWeekend(data);
        } else {
            sec1Title.textContent = 'BATTLE TRACKER';
            sec2Title.textContent = 'PIT STOPS';
            drsBadge.classList.remove('hidden');
            histTag.classList.add('hidden');
            rcBadge.textContent = '● LIVE';
            radioBadge.textContent = '● LISTENING';
            document.getElementById('sec1-content').className = 'battle-list';
            document.getElementById('sec2-content').className = 'pit-list';
            if (data.live_timing) this.updateTiming(data.live_timing, data.circuit_info, data.current_race?.session_name);
            this.updateLiveBottom(data);
        }
    }

    // ======================================================
    // LIVE BOTTOM (battle tracker + pit stops)
    // ======================================================
    updateLiveBottom(data) {
        if (data.live_timing) this.updateBattleTracker(data.live_timing);
        if (data.pit_stops !== undefined) this.updatePitStops(data.pit_stops);
    }

    // ======================================================
    // OFF-WEEKEND FULL UPDATE
    // ======================================================
    updateOffWeekend(data) {
        if (data.last_race) this.updatePodium(data.last_race);
        if (data.circuit_info || data.next_race) this.updateCircuitProfile(data.circuit_info, data.next_race);
        if (data.circuit_info) this.updateCircuitDNA(data.circuit_info);
        if (data.season_stats) this.updateSeasonStats(data.season_stats);
        if (data.standings) this.updateTeammateBattles(data.standings);
        if (data.circuit_history) this.updateCircuitHistory(data.circuit_history, data.next_race);
        if (data.driver_form && data.standings) this.updateDriverForm(data.driver_form, data.standings);
    }

    // ======================================================
    // SESSION BADGE
    // ======================================================
    updateSessionBadge(race) {
        document.getElementById('session-name').textContent = race.session_name || 'STANDBY';
        document.getElementById('session-circuit').textContent =
            `${race.flag || ''} ${race.name || ''} — ${race.location || ''}, ${race.country || ''}`;
        const dot = document.getElementById('session-live-dot');
        dot.classList.toggle('live-dot-active', !!race.is_live);
    }

    updateCurrentRace(race) {
        document.getElementById('current-flag').textContent = race.flag || '🏁';
        document.getElementById('current-gp-name').textContent = race.name || '—';
        document.getElementById('current-circuit').textContent =
            `${race.location || ''}, ${race.country || ''}`;
        const el = document.getElementById('current-session-type');
        el.textContent = race.session_name || '—';
        el.className = 'race-card-session' + (race.is_live ? ' session-live' : '');
    }

    updateNextRace(race) {
        if (!race) {
            document.getElementById('next-gp-name').textContent = 'TBA';
            document.getElementById('next-circuit').textContent = '';
            document.getElementById('next-race-date').textContent = '—';
            document.getElementById('next-flag').textContent = '🏁';
            return;
        }
        document.getElementById('next-flag').textContent = race.flag || '🏁';
        document.getElementById('next-gp-name').textContent = race.name || '—';
        document.getElementById('next-circuit').textContent =
            `${race.location || ''}, ${race.country || ''}`;

        const timeUtc = (race.time_utc || '').replace('Z', '');
        const d = new Date(race.date + 'T' + (timeUtc || '00:00:00') + 'Z');
        document.getElementById('next-race-date').textContent =
            d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });

        if (timeUtc) {
            const istMs = d.getTime() + (5.5 * 3600000);
            const istD = new Date(istMs);
            document.getElementById('next-race-ist').textContent =
                `IST ${istD.getUTCHours().toString().padStart(2, '0')}:${istD.getUTCMinutes().toString().padStart(2, '0')}`;
        }
        this.nextRaceDate = d;
    }

    // ======================================================
    // WEEKEND SCHEDULE
    // ======================================================
    updateWeekendSchedule(schedule) {
        const container = document.getElementById('weekend-schedule');
        if (!schedule || !schedule.length) {
            container.innerHTML = '<div class="empty-state">No sessions available</div>';
            return;
        }
        container.innerHTML = '';
        schedule.forEach(s => {
            const row = document.createElement('div');
            row.className = `schedule-row status-${s.status.toLowerCase()}`;
            let startStr = '—', istStr = '';
            try {
                const d = new Date(s.date_start);
                startStr = d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })
                    + ' · ' + d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false });
                const utcMs = d.getTime() + (d.getTimezoneOffset() * 60000);
                const istD = new Date(utcMs + (5.5 * 3600000));
                istStr = `IST ${istD.getUTCHours().toString().padStart(2,'0')}:${istD.getUTCMinutes().toString().padStart(2,'0')}`;
            } catch {}
            row.innerHTML = `
                <div class="sched-name">${s.name}</div>
                <div class="sched-time">${startStr}</div>
                <div class="sched-ist">${istStr}</div>
                <div class="sched-status ${s.status === 'LIVE' ? 'sched-live' : s.status === 'COMPLETED' ? 'sched-done' : 'sched-upcoming'}">
                    ${s.status === 'LIVE' ? '● LIVE' : s.status === 'COMPLETED' ? '✓' : '○'}
                </div>
            `;
            container.appendChild(row);
        });
    }

    // ======================================================
    // WEATHER
    // ======================================================
    updateWeather(trackW, extW) {
        const airTemp     = extW ? extW.air_temp     : (trackW ? trackW.air_temp   : '--');
        const condition   = extW ? extW.condition    : (trackW ? trackW.condition  : '--');
        const humidity    = extW ? extW.humidity     : (trackW ? trackW.humidity   : '--');
        const windSpeed   = extW ? extW.wind_speed   : (trackW ? trackW.wind_speed : '--');
        const precip      = extW ? extW.precipitation : (trackW ? (trackW.rainfall || 0) : 0);
        const feelsLike   = extW ? extW.feels_like   : airTemp;
        const cloudCover  = extW ? extW.cloud_cover  : '--';
        const windDir     = extW ? extW.wind_direction : (trackW ? trackW.wind_direction : '--');
        // Estimate track temp from air temp (+15°C average offset) when OpenF1 sensor unavailable
        const rawTrackTemp = trackW ? trackW.track_temp : '--';
        const trackTemp = (rawTrackTemp !== '--' && rawTrackTemp !== null)
            ? rawTrackTemp
            : (extW ? Math.round(extW.air_temp + 15) : '--');

        document.getElementById('weather-air-temp').textContent = airTemp !== '--' ? `${airTemp}°C` : '--°C';
        document.getElementById('weather-feels').textContent = feelsLike !== '--' ? `Feels like ${feelsLike}°C` : '';
        document.getElementById('weather-cond').textContent = condition || '—';
        document.getElementById('weather-track-temp').textContent = trackTemp !== '--' ? `~${trackTemp}°C` : '--°C';
        document.getElementById('weather-humidity').textContent = humidity !== '--' ? `${humidity}%` : '--%';
        document.getElementById('weather-wind').textContent = windSpeed !== '--' ? `${windSpeed} m/s` : '-- m/s';
        document.getElementById('weather-cloud').textContent = cloudCover !== '--' ? `${cloudCover}%` : '--%';
        document.getElementById('weather-dir').textContent = windDir !== '--' ? `${windDir}°${this.windDirArrow(windDir)}` : '--°';

        const rainEl = document.getElementById('weather-rain');
        if (precip && precip > 0) { rainEl.textContent = `${precip}mm`; rainEl.style.color = '#4da6ff'; }
        else { rainEl.textContent = 'NONE'; rainEl.style.color = ''; }

        const iconEl = document.getElementById('weather-icon');
        const cond = (condition || '').toLowerCase();
        if (cond.includes('thunder')) iconEl.textContent = '⛈';
        else if (cond.includes('snow')) iconEl.textContent = '❄';
        else if (cond.includes('shower') || cond.includes('drizzle')) iconEl.textContent = '🌦';
        else if (cond.includes('rain')) iconEl.textContent = '🌧';
        else if (cond.includes('fog')) iconEl.textContent = '🌫';
        else if (cond.includes('overcast') || cond.includes('cloudy')) iconEl.textContent = '☁';
        else if (cond.includes('partly')) iconEl.textContent = '⛅';
        else if (cond.includes('clear') || cond.includes('mainly')) iconEl.textContent = '☀';
        else iconEl.textContent = '🌤';

        const srcEl = document.getElementById('weather-source');
        if (extW) { srcEl.textContent = extW.timezone_abbr || 'LIVE'; srcEl.style.color = '#00cc44'; }
        else if (trackW && trackW.condition !== 'No Data') { srcEl.textContent = 'F1 SENSOR'; srcEl.style.color = '#ffaa00'; }
        else { srcEl.textContent = 'NO DATA'; srcEl.style.color = '#888'; }
    }

    windDirArrow(deg) {
        const dirs = ['↑N','↗NE','→E','↘SE','↓S','↙SW','←W','↖NW'];
        return ' ' + dirs[Math.round(deg / 45) % 8];
    }

    // ======================================================
    // LIVE TIMING
    // ======================================================
    updateTiming(timingData, circuitInfo, sessionName) {
        const container = document.getElementById('timing-grid');
        if (!timingData || !timingData.length) {
            container.innerHTML = '<div class="timing-loading">Waiting for session data...</div>';
            this._hideLapProgress();
            return;
        }
        let fastestSecs = Infinity, fastestDriver = -1;
        timingData.forEach(d => {
            const s = this.timeToSecs(d.time);
            if (s > 0 && s < fastestSecs) { fastestSecs = s; fastestDriver = d.driver_number; }
        });
        const maxLap = Math.max(...timingData.map(d => d.lap_number || 0), 0);
        if (maxLap > 0) document.getElementById('lap-counter').textContent = `LAP ${maxLap}`;

        // Lap progress bar — only during Race sessions
        const isRace = (sessionName || '').toLowerCase().includes('race');
        const totalLaps = circuitInfo ? circuitInfo.laps : 0;
        if (isRace && maxLap > 0 && totalLaps > 0) {
            const pct = Math.min(100, (maxLap / totalLaps) * 100);
            const wrap = document.getElementById('lap-progress-wrap');
            const fill = document.getElementById('lap-progress-fill');
            const lbl = document.getElementById('lap-progress-label');
            wrap.classList.remove('hidden');
            fill.style.width = `${pct.toFixed(1)}%`;
            lbl.textContent = `LAP ${maxLap} / ${totalLaps}`;
        } else {
            this._hideLapProgress();
        }

        container.innerHTML = '';
        timingData.forEach((driver, index) => {
            const isLeader = index === 0;
            const isFastest = driver.driver_number === fastestDriver && fastestDriver !== -1;
            const tc = driver.team_colour || 'ffffff';
            const row = document.createElement('div');
            row.className = `timing-row${isLeader ? ' leader' : ''}${isFastest ? ' fastest-lap' : ''}`;
            row.style.setProperty('--team-color', `#${tc}`);

            const prev = this.lastTimingData[driver.driver_number];
            if (prev && (prev.position !== driver.position || prev.time !== driver.time)) {
                row.classList.add('flash');
                setTimeout(() => row.classList.remove('flash'), 600);
            }

            // Position delta
            const prevPos = this.prevPositions[driver.driver_number];
            let deltaHtml = '<div class="pos-delta"></div>';
            if (prevPos !== undefined && prevPos !== driver.position) {
                const gained = prevPos - driver.position;
                if (gained > 0) deltaHtml = `<div class="pos-delta delta-up">▲${gained}</div>`;
                else if (gained < 0) deltaHtml = `<div class="pos-delta delta-down">▼${Math.abs(gained)}</div>`;
            }
            this.prevPositions[driver.driver_number] = driver.position;
            this.lastTimingData[driver.driver_number] = { position: driver.position, time: driver.time };

            let intStr = '--';
            if (isLeader) intStr = 'LEADER';
            else if (driver.interval !== undefined && driver.interval !== null) {
                intStr = typeof driver.interval === 'number' ? `+${driver.interval.toFixed(3)}` : driver.interval;
            }

            row.innerHTML = `
                <div class="pos">${driver.position}</div>
                ${deltaHtml}
                <div class="drv-num" style="background:#${tc}22;color:#${tc};border-color:#${tc}44">${driver.driver_number}</div>
                <div class="drv-info">
                    <span class="drv-name">${driver.broadcast_name || driver.name_acronym}</span>
                    <span class="drv-team" style="color:#${tc}">${(driver.team_name || '').toUpperCase()}</span>
                </div>
                <div class="timing-time${isFastest ? ' purple' : ''}">${driver.time}</div>
                <div class="timing-gap">${driver.gap}</div>
                <div class="timing-int${intStr === 'LEADER' ? ' int-leader' : ''}">${intStr}</div>
                <div class="tyre-cell">${this.tyreHtml((driver.compound || '').toUpperCase())}</div>
                <div class="lap-cell">L${driver.lap_number || '-'}</div>
            `;
            container.appendChild(row);
        });

        const statusEl = document.getElementById('timing-status');
        if (timingData[0] && timingData[0].lap_number) {
            statusEl.textContent = `LAP ${timingData[0].lap_number}`;
            statusEl.style.background = '#e10600';
            statusEl.style.color = '#fff';
        }
    }

    tyreHtml(compound) {
        const map = {
            'SOFT': { color: '#ff2222', bg: '#3a0000', label: 'S' },
            'MEDIUM': { color: '#ffdd00', bg: '#2a2500', label: 'M' },
            'HARD': { color: '#e8e8e8', bg: '#1e1e1e', label: 'H' },
            'INTERMEDIATE': { color: '#00cc55', bg: '#002a10', label: 'I' },
            'WET': { color: '#3399ff', bg: '#001a3a', label: 'W' },
        };
        const t = map[compound];
        if (!t) return `<span class="tyre-badge" style="color:#888;border-color:#333">—</span>`;
        return `<span class="tyre-badge" style="color:${t.color};background:${t.bg};border-color:${t.color}">${t.label}</span>`;
    }

    timeToSecs(str) {
        if (!str || str === '--') return 0;
        const p = str.split(':');
        return p.length === 2 ? parseFloat(p[0]) * 60 + parseFloat(p[1]) : 0;
    }

    // ======================================================
    // BATTLE TRACKER (live mode sec1)
    // ======================================================
    updateBattleTracker(timingData) {
        const container = document.getElementById('sec1-content');
        if (!timingData || timingData.length < 2) {
            container.innerHTML = '<div class="empty-state">No close battles detected</div>';
            return;
        }
        const battles = [];
        for (let i = 1; i < timingData.length; i++) {
            const ahead = timingData[i - 1];
            const behind = timingData[i];
            const gA = this.parseGap(ahead.gap);
            const gB = this.parseGap(behind.gap);
            if (gA === null || gB === null) continue;
            const diff = gB - gA;
            if (diff >= 0 && diff <= 1.0) battles.push({ ahead, behind, gap: diff });
        }
        if (!battles.length) {
            container.innerHTML = '<div class="empty-state">No battles within 1s</div>';
            document.getElementById('drs-badge').classList.remove('drs-active');
            document.getElementById('drs-badge').textContent = 'DRS';
            return;
        }
        container.innerHTML = '';
        battles.forEach(b => {
            const card = document.createElement('div');
            card.className = 'battle-card';
            const aheadTc = b.ahead.team_colour || 'ffffff';
            const behindTc = b.behind.team_colour || 'ffffff';
            card.innerHTML = `
                <div class="battle-driver">
                    <span class="battle-pos" style="color:#${aheadTc}">P${b.ahead.position}</span>
                    <span class="battle-name" style="color:#${aheadTc}">${b.ahead.name_acronym}</span>
                    <span>${this.tyreHtml((b.ahead.compound||'').toUpperCase())}</span>
                </div>
                <div class="battle-gap-bar">
                    <div class="battle-gap-fill" style="width:${Math.max(5,(1-b.gap)*100)}%"></div>
                    <span class="battle-gap-val ${b.gap < 0.5 ? 'gap-hot' : ''}">+${b.gap.toFixed(3)}s</span>
                </div>
                <div class="battle-driver">
                    <span class="battle-pos" style="color:#${behindTc}">P${b.behind.position}</span>
                    <span class="battle-name" style="color:#${behindTc}">${b.behind.name_acronym}</span>
                    <span>${this.tyreHtml((b.behind.compound||'').toUpperCase())}</span>
                </div>
            `;
            container.appendChild(card);
        });
        const badge = document.getElementById('drs-badge');
        badge.classList.add('drs-active');
        badge.textContent = `DRS ${battles.length}`;
    }

    parseGap(gapStr) {
        if (!gapStr || gapStr === '--' || gapStr.includes('LAP')) return null;
        if (gapStr === '+0.000' || gapStr === '+0') return 0;
        const n = parseFloat(gapStr.replace('+', ''));
        return isNaN(n) ? null : n;
    }

    // ======================================================
    // RACE ALERT BANNER
    // ======================================================
    updateAlertBanner(messages) {
        const banner = document.getElementById('race-alert');
        if (!messages || !messages.length) return;
        const sorted = [...messages].sort((a, b) => (b.timestamp || '') > (a.timestamp || '') ? 1 : -1);
        const recent = sorted.slice(0, 5);

        const scMsg  = recent.find(m => (m.category||'').toLowerCase().includes('safetycar') || (m.message||'').toLowerCase().includes('safety car'));
        const vscMsg = recent.find(m => (m.message||'').toLowerCase().includes('virtual safety car'));
        const rfMsg  = recent.find(m => (m.flag||'').includes('RED') || (m.message||'').toLowerCase().includes('red flag'));
        const yfMsg  = recent.find(m => (m.flag||'').includes('YELLOW'));

        let icon = '', text = '', alertClass = '';
        if (rfMsg) { icon = '🔴'; text = rfMsg.message || 'RED FLAG'; alertClass = 'alert-red-flag'; }
        else if (scMsg) { icon = '🚗'; text = scMsg.message || 'SAFETY CAR DEPLOYED'; alertClass = 'alert-safety-car'; }
        else if (vscMsg) { icon = '🟡'; text = vscMsg.message || 'VIRTUAL SAFETY CAR'; alertClass = 'alert-vsc'; }
        else if (yfMsg) { icon = '⚡'; text = yfMsg.message || 'YELLOW FLAG'; alertClass = 'alert-yellow'; }

        if (text) {
            document.getElementById('alert-icon').textContent = icon;
            document.getElementById('alert-text').textContent = text;
            banner.className = `race-alert ${alertClass}`;
        }
    }

    // ======================================================
    // CHAMPIONSHIP STANDINGS
    // ======================================================
    updateStandings(standings) {
        this.renderDriverStandings(standings.drivers || []);
        this.renderConstructorStandings(standings.constructors || []);
        this.renderTitleFight(standings.drivers || []);
    }

    renderDriverStandings(list) {
        const container = document.getElementById('driver-standings');
        if (!list.length) { container.innerHTML = '<div class="empty-state">Loading standings...</div>'; return; }
        const maxPts = Math.max(...list.map(s => s.points || 0), 1);
        container.innerHTML = '';
        list.forEach(s => {
            const tc = s.team_colour || 'ffffff';
            const pct = ((s.points || 0) / maxPts * 100).toFixed(1);
            const item = document.createElement('div');
            item.className = 'standing-item';
            item.style.borderLeftColor = `#${tc}`;
            item.innerHTML = `
                <div class="st-pos">${s.position}</div>
                <div class="st-info">
                    <span class="st-name">${s.name || s.name_acronym || '—'}</span>
                    <span class="st-team" style="color:#${tc}">${(s.team_name || '').toUpperCase()}</span>
                </div>
                <div class="st-meta">
                    <span class="st-pts">${s.points} PTS</span>
                    ${s.wins ? `<span class="st-wins">${s.wins}W</span>` : ''}
                </div>
                <div class="pts-bar-bg"><div class="pts-bar" style="width:${pct}%;background:#${tc}40;border-right:2px solid #${tc}"></div></div>
            `;
            container.appendChild(item);
        });
    }

    renderConstructorStandings(list) {
        const container = document.getElementById('constructor-standings');
        if (!list.length) { container.innerHTML = '<div class="empty-state">Loading standings...</div>'; return; }
        const maxPts = Math.max(...list.map(s => s.points || 0), 1);
        container.innerHTML = '';
        list.forEach((s, i) => {
            const tc = s.team_colour || 'ffffff';
            const pct = ((s.points || 0) / maxPts * 100).toFixed(1);
            const item = document.createElement('div');
            item.className = 'standing-item';
            item.style.borderLeftColor = `#${tc}`;
            item.innerHTML = `
                <div class="st-pos">${s.position || i + 1}</div>
                <div class="st-info"><span class="st-name">${(s.team_name || 'Unknown').toUpperCase()}</span></div>
                <div class="st-meta">
                    <span class="st-pts">${s.points} PTS</span>
                    ${s.wins ? `<span class="st-wins">${s.wins}W</span>` : ''}
                </div>
                <div class="pts-bar-bg"><div class="pts-bar" style="width:${pct}%;background:#${tc}40;border-right:2px solid #${tc}"></div></div>
            `;
            container.appendChild(item);
        });
    }

    updateCalendar(calendar) {
        const container = document.getElementById('calendar-list');
        if (!calendar || !calendar.length) { container.innerHTML = '<div class="empty-state">Calendar loading...</div>'; return; }
        container.innerHTML = '';
        calendar.forEach(r => {
            const item = document.createElement('div');
            item.className = `cal-item${r.status === 'COMPLETED' ? ' cal-done' : ''}`;
            item.innerHTML = `
                <div class="cal-round">R${r.round}</div>
                <div class="cal-info">
                    <span class="cal-flag">${r.flag || '🏁'}</span>
                    <div class="cal-names">
                        <span class="cal-name">${r.name}</span>
                        <span class="cal-loc">${r.location}, ${r.country}</span>
                    </div>
                </div>
                <div class="cal-date">${r.date}</div>
                <div class="cal-status ${r.status === 'COMPLETED' ? 'cal-status-done' : 'cal-status-upcoming'}">
                    ${r.status === 'COMPLETED' ? '✓' : '○'}
                </div>
            `;
            container.appendChild(item);
        });
    }

    // ======================================================
    // PIT STOPS (live mode sec2)
    // ======================================================
    updatePitStops(pits) {
        const container = document.getElementById('sec2-content');
        if (!pits || !pits.length) {
            container.innerHTML = '<div class="empty-state">No pit stops this session</div>';
            return;
        }
        let fastestDur = Infinity;
        pits.forEach(p => { const d = parseFloat(p.duration_raw); if (!isNaN(d) && d < fastestDur) fastestDur = d; });
        container.innerHTML = '';
        [...pits].reverse().forEach(p => {
            const tc = p.team_colour || 'ffffff';
            const isFastest = !isNaN(parseFloat(p.duration_raw)) && parseFloat(p.duration_raw) <= fastestDur;
            const row = document.createElement('div');
            row.className = 'pit-row';
            row.style.borderLeftColor = `#${tc}`;
            row.innerHTML = `
                <span class="pit-driver" style="color:#${tc}">${p.driver}</span>
                <span class="pit-lap">L${p.lap}</span>
                <span class="pit-dur${isFastest ? ' pit-fastest' : ''}">${p.duration}${isFastest ? ' ⚡' : ''}</span>
            `;
            container.appendChild(row);
        });
    }

    // ======================================================
    // RACE CONTROL
    // ======================================================
    updateRaceControl(messages) {
        const container = document.getElementById('race-control');
        if (!messages || !messages.length) {
            container.innerHTML = '<div class="empty-state">Awaiting race control messages</div>';
            return;
        }
        container.innerHTML = '';
        [...messages].reverse().forEach(msg => {
            const flag = (msg.flag || '').toUpperCase();
            const cat  = (msg.category || '').toLowerCase();
            let icon = '⚠', cls = '';
            if (flag.includes('YELLOW')) { cls = 'rc-yellow'; icon = '🟡'; }
            else if (flag.includes('RED')) { cls = 'rc-red'; icon = '🔴'; }
            else if (flag.includes('GREEN')) { cls = 'rc-green'; icon = '🟢'; }
            else if (flag.includes('CHEQUERED')) icon = '🏁';
            if (cat.includes('safetycar')) { cls = 'rc-sc'; icon = '🚗'; }
            else if (cat === 'drs') icon = '📡';
            let ts = '';
            try { ts = new Date(msg.timestamp).toLocaleTimeString('en-GB', { hour12: false }); } catch {}
            const item = document.createElement('div');
            item.className = `rc-item ${cls}`;
            item.innerHTML = `<span class="rc-icon">${icon}</span><span class="rc-time">${ts}</span><span class="rc-msg">${msg.message}</span>`;
            container.appendChild(item);
        });
    }

    // ======================================================
    // TEAM RADIO
    // ======================================================
    updateRadio(radioLog) {
        const container = document.getElementById('team-radio');
        if (!radioLog || !radioLog.length) {
            container.innerHTML = '<div class="empty-state">Team radio will appear during sessions</div>';
            return;
        }
        container.innerHTML = '';
        [...radioLog].reverse().forEach(msg => {
            const tc = msg.team_colour || 'ffffff';
            let ts = '';
            try { ts = new Date(msg.timestamp).toLocaleTimeString('en-GB', { hour12: false }); } catch {}
            const item = document.createElement('div');
            item.className = 'radio-item';
            item.style.borderLeftColor = `#${tc}`;
            item.innerHTML = `
                <span class="radio-ts">${ts}</span>
                <span class="radio-drv" style="color:#${tc}">📻 ${msg.driver}</span>
                <span class="radio-msg">${msg.message}</span>
                ${msg.recording_url ? `<a href="${msg.recording_url}" target="_blank" class="radio-play" title="Listen">▶</a>` : ''}
            `;
            container.appendChild(item);
        });
    }

    // ======================================================
    // OFF-WEEKEND: PODIUM
    // ======================================================
    updatePodium(lastRace) {
        document.getElementById('ow-panel-title').textContent = 'LAST RACE HIGHLIGHTS';
        document.getElementById('ow-race-tag').textContent =
            `${lastRace.flag || '🏁'} ${lastRace.name || ''} — R${lastRace.round || ''}`;

        const podium = lastRace.podium || [];

        const renderCard = (pos, codeId, teamId, timeId) => {
            const p = podium.find(x => x.position === pos);
            if (!p) return;
            const tc = p.team_colour || 'ffffff';
            document.getElementById(codeId).textContent = p.code || p.driver;
            document.getElementById(codeId).style.color = `#${tc}`;
            document.getElementById(teamId).textContent = p.team;
            document.getElementById(teamId).style.color = `#${tc}88`;
            document.getElementById(timeId).textContent = p.time || p.status;
        };

        renderCard(1, 'p1-code', 'p1-team', 'p1-time');
        renderCard(2, 'p2-code', 'p2-team', 'p2-time');
        renderCard(3, 'p3-code', 'p3-team', 'p3-time');

        if (podium[0]) {
            const tc = podium[0].team_colour || 'ffffff';
            document.getElementById('podium-p1').style.setProperty('--winner-color', `#${tc}`);
            document.getElementById('p1-pts').textContent = `+${podium[0].points} PTS`;
        }

        // Stats bar
        const fl = lastRace.fastest_lap;
        if (fl) {
            document.getElementById('fastest-drv').textContent = fl.driver;
            document.getElementById('fastest-drv').style.color = `#${fl.team_colour || 'c060ff'}`;
            document.getElementById('fastest-time').textContent =
                `${fl.time}${fl.speed_kph ? ` · ${fl.speed_kph}km/h` : ''}`;
        }
        document.getElementById('race-entries').textContent = lastRace.total_entries || '—';

        if (lastRace.date) {
            const d = new Date(lastRace.date + 'T12:00:00Z');
            document.getElementById('last-race-date').textContent =
                d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
        }
        document.getElementById('last-race-loc').textContent =
            `${lastRace.flag || ''} ${lastRace.location || ''}, ${lastRace.country || ''}`;

        // Full grid
        const grid = document.getElementById('last-race-grid');
        grid.innerHTML = '';
        const medals = { '1': '🥇', '2': '🥈', '3': '🥉' };
        (lastRace.all_results || []).forEach(r => {
            const tc = r.team_colour || 'ffffff';
            const pos = String(r.position);
            const row = document.createElement('div');
            row.className = `lg-row${parseInt(pos) <= 3 ? ' lg-top3' : ''}`;
            row.style.borderLeftColor = `#${tc}`;
            const posDisplay = medals[pos] || pos;
            row.innerHTML = `
                <span class="lg-pos">${posDisplay}</span>
                <span class="lg-code" style="color:#${tc}">${r.code}</span>
                <span class="lg-team">${r.team}</span>
                <span class="lg-time">${r.time || r.status}</span>
                <span class="lg-pts">${r.points > 0 ? '+' + r.points : ''}</span>
            `;
            grid.appendChild(row);
        });
    }

    // ======================================================
    // OFF-WEEKEND: CIRCUIT PROFILE
    // ======================================================
    updateCircuitProfile(info, nextRace) {
        if (nextRace) {
            document.getElementById('cp-flag').textContent = nextRace.flag || '🏁';
            document.getElementById('cp-name').textContent = nextRace.circuit || nextRace.name || '—';
            document.getElementById('cp-location').textContent =
                `${nextRace.location || ''}, ${nextRace.country || ''}`;
        }
        if (!info) {
            ['cs-laps','cs-length','cs-drs','cs-corners','cs-distance','cs-record','cs-record-holder','cs-first-gp','cs-surface']
                .forEach(id => document.getElementById(id).textContent = '—');
            return;
        }
        document.getElementById('cs-laps').textContent = info.laps || '—';
        document.getElementById('cs-length').textContent = info.length_km ? `${info.length_km}` : '—';
        document.getElementById('cs-drs').textContent = info.drs_zones ?? '—';
        document.getElementById('cs-corners').textContent = info.corners || '—';
        document.getElementById('cs-distance').textContent =
            (info.laps && info.length_km) ? (info.laps * info.length_km).toFixed(1) : '—';
        document.getElementById('cs-record').textContent = info.lap_record || '—';
        document.getElementById('cs-record-holder').textContent =
            info.record_holder ? `${info.record_holder} (${info.record_year})` : '—';
        document.getElementById('cs-first-gp').textContent = info.first_gp || '—';
        document.getElementById('cs-surface').textContent = info.surface || '—';
    }

    // ======================================================
    // OFF-WEEKEND: CIRCUIT DNA
    // ======================================================
    updateCircuitDNA(info) {
        const dna = info && info.dna;
        const section = document.getElementById('circuit-dna');
        if (!section) return;

        if (!dna) { section.style.display = 'none'; return; }
        section.style.display = '';

        const setBar = (fillId, valId, value, max = 10) => {
            const pct = Math.round((value / max) * 100);
            const fill = document.getElementById(fillId);
            const val = document.getElementById(valId);
            if (fill) fill.style.width = `${pct}%`;
            if (val) {
                const level = value <= 3 ? 'LOW' : value <= 6 ? 'MED' : value <= 8 ? 'HIGH' : 'MAX';
                val.textContent = level;
            }
        };

        setBar('dna-fill-speed', 'dna-val-speed', dna.speed || 5);
        setBar('dna-fill-wear', 'dna-val-wear', dna.tire_wear || 5);
        setBar('dna-fill-overtaking', 'dna-val-overtaking', dna.overtaking || 5);

        const badge = document.getElementById('dna-downforce-badge');
        if (badge && dna.downforce) {
            badge.textContent = dna.downforce.toUpperCase();
            const colorMap = { 'LOW': '#00cc44', 'MEDIUM': '#ffaa00', 'HIGH': '#e10600', 'EXTREME': '#c060ff' };
            badge.style.background = (colorMap[dna.downforce.toUpperCase()] || '#888') + '22';
            badge.style.color = colorMap[dna.downforce.toUpperCase()] || '#888';
            badge.style.borderColor = colorMap[dna.downforce.toUpperCase()] || '#888';
        }
    }

    // ======================================================
    // OFF-WEEKEND: DRIVER FORM GUIDE
    // ======================================================
    updateDriverForm(form, standings) {
        const container = document.getElementById('form-guide-list');
        if (!container) return;

        const drivers = (standings.drivers || []).slice(0, 8);
        if (!drivers.length || !Object.keys(form).length) {
            container.innerHTML = '<div class="empty-state">Form data loading...</div>';
            return;
        }

        const posColor = (p) => {
            if (p === 'DNF') return { bg: '#ff222244', color: '#ff4444', text: '✕' };
            const n = parseInt(p);
            if (n === 1) return { bg: '#ffd70033', color: '#ffd700', text: '1' };
            if (n <= 3) return { bg: '#00cc4433', color: '#00cc44', text: String(n) };
            if (n <= 10) return { bg: '#00d4ff22', color: '#00d4ff', text: String(n) };
            return { bg: '#33333388', color: '#7a7a8a', text: String(n) };
        };

        container.innerHTML = '';
        drivers.forEach(d => {
            const tc = d.team_colour || 'ffffff';
            const driverForm = form[d.name_acronym] || form[d.name_acronym?.toUpperCase()] || null;
            const positions = driverForm ? driverForm.positions : [];

            const badges = positions.length
                ? positions.map(p => {
                    const c = posColor(p);
                    return `<span class="form-result" style="background:${c.bg};color:${c.color};border-color:${c.color}88">${c.text}</span>`;
                }).join('')
                : '<span class="form-no-data">—</span>';

            const row = document.createElement('div');
            row.className = 'form-driver-row';
            row.innerHTML = `
                <span class="form-code" style="color:#${tc}">${d.name_acronym || '—'}</span>
                <span class="form-pos-num">${d.position}</span>
                <div class="form-badges">${badges}</div>
                <span class="form-pts">${d.points}p</span>
            `;
            container.appendChild(row);
        });
    }

    // ======================================================
    // OFF-WEEKEND: SEASON STATS
    // ======================================================
    updateSeasonStats(stats) {
        const completed = stats.completed || 0;
        const total = stats.total || 0;
        const pct = total > 0 ? ((completed / total) * 100).toFixed(0) : 0;

        document.getElementById('ss-fill').style.width = `${pct}%`;
        document.getElementById('ss-progress-text').textContent = `${completed} / ${total} RACES COMPLETE`;

        if (stats.leader) {
            const tc = stats.leader.team_colour || 'ffffff';
            document.getElementById('ss-leader').innerHTML =
                `<span style="color:#${tc};font-weight:700">${stats.leader.name_acronym || stats.leader.name}</span>` +
                ` <span style="font-size:0.65rem;color:#${tc}88">${(stats.leader.team_name||'').toUpperCase()}</span>` +
                ` <span class="ss-pts">${stats.leader.points} PTS</span>` +
                (stats.leader.wins ? ` <span class="ss-wins">${stats.leader.wins} WINS</span>` : '');
        }
        if (stats.points_gap !== null && stats.second) {
            const tc2 = stats.second.team_colour || 'ffffff';
            document.getElementById('ss-gap').innerHTML =
                `<span class="ss-gap-label">P2 GAP:</span> ` +
                `<span style="color:#${tc2}">${stats.second.name_acronym || stats.second.name}</span> ` +
                `<span class="ss-gap-val">−${stats.points_gap} PTS</span>`;
        }
    }

    // ======================================================
    // OFF-WEEKEND: TEAMMATE BATTLES (sec1 in off mode)
    // ======================================================
    updateTeammateBattles(standings) {
        const container = document.getElementById('sec1-content');
        const drivers = standings.drivers || [];
        if (!drivers.length) {
            container.innerHTML = '<div class="empty-state">Standings loading...</div>';
            return;
        }

        // Group drivers by team
        const teams = {};
        drivers.forEach(d => {
            if (!teams[d.team_name]) teams[d.team_name] = [];
            teams[d.team_name].push(d);
        });

        const battles = Object.entries(teams)
            .filter(([, drivers]) => drivers.length >= 2)
            .map(([teamName, drvs]) => ({
                team: teamName,
                tc: drvs[0].team_colour || 'ffffff',
                ahead: drvs[0],
                behind: drvs[1],
                gap: Math.abs((drvs[0].points || 0) - (drvs[1].points || 0)),
                total: Math.max(drvs[0].points || 0, 1),
            }));

        if (!battles.length) {
            container.innerHTML = '<div class="empty-state">No teammate data</div>';
            return;
        }

        container.innerHTML = '';
        battles.forEach(b => {
            const card = document.createElement('div');
            card.className = 'teammate-card';
            card.style.borderLeftColor = `#${b.tc}`;

            const aheadPct = 100;
            const behindPct = Math.max(5, ((b.behind.points || 0) / (b.ahead.points || 1)) * 100);

            card.innerHTML = `
                <div class="tm-team" style="color:#${b.tc}">${b.team.toUpperCase()}</div>
                <div class="tm-row">
                    <span class="tm-driver tm-ahead" style="color:#${b.tc}">${b.ahead.name_acronym}</span>
                    <div class="tm-bars">
                        <div class="tm-bar-wrap">
                            <div class="tm-bar" style="width:${aheadPct}%;background:#${b.tc}"></div>
                            <span class="tm-pts">${b.ahead.points}</span>
                        </div>
                        <div class="tm-bar-wrap tm-bar-behind">
                            <div class="tm-bar" style="width:${behindPct}%;background:#${b.tc}44"></div>
                            <span class="tm-pts">${b.behind.points}</span>
                        </div>
                    </div>
                    <span class="tm-driver tm-behind" style="color:#${b.tc}88">${b.behind.name_acronym}</span>
                </div>
                <div class="tm-gap">GAP: <span style="color:#${b.tc}">+${b.gap}</span> PTS</div>
            `;
            container.appendChild(card);
        });
    }

    // ======================================================
    // LAP PROGRESS HELPER
    // ======================================================
    _hideLapProgress() {
        const wrap = document.getElementById('lap-progress-wrap');
        if (wrap) wrap.classList.add('hidden');
    }

    // ======================================================
    // TITLE FIGHT CARD
    // ======================================================
    renderTitleFight(drivers) {
        const card = document.getElementById('title-fight-card');
        if (!card) return;
        if (drivers.length < 2) { card.classList.add('hidden'); return; }

        const p1 = drivers[0];
        const p2 = drivers[1];
        const maxPts = Math.max(p1.points || 0, 1);
        const gap = (p1.points || 0) - (p2.points || 0);
        const tc1 = p1.team_colour || 'e10600';
        const tc2 = p2.team_colour || '7a7a8a';

        document.getElementById('tf-p1-code').textContent = p1.name_acronym || '—';
        document.getElementById('tf-p2-code').textContent = p2.name_acronym || '—';
        document.getElementById('tf-p1-pts').textContent = `${p1.points} PTS`;
        document.getElementById('tf-p2-pts').textContent = `${p2.points} PTS`;
        document.getElementById('tf-p1-wins').textContent = p1.wins ? `${p1.wins}W` : '';
        document.getElementById('tf-p2-wins').textContent = p2.wins ? `${p2.wins}W` : '';
        document.getElementById('tf-gap-label').textContent =
            gap > 0 ? `+${gap} PTS LEAD` : gap === 0 ? 'LEVEL ON POINTS' : `${gap} PTS`;

        const p1Bar = document.getElementById('tf-p1-bar');
        const p2Bar = document.getElementById('tf-p2-bar');
        p1Bar.style.cssText = `width:100%;background:#${tc1}`;
        p2Bar.style.cssText = `width:${Math.max(8, ((p2.points || 0) / maxPts * 100)).toFixed(1)}%;background:#${tc2}`;

        document.getElementById('tf-p1-row').style.borderLeftColor = `#${tc1}`;
        document.getElementById('tf-p2-row').style.borderLeftColor = `#${tc2}`;

        card.classList.remove('hidden');
    }

    // ======================================================
    // RACE WEEKEND FORECAST
    // ======================================================
    updateForecast(forecast, race) {
        const block = document.getElementById('forecast-block');
        const strip = document.getElementById('forecast-strip');
        const locEl = document.getElementById('forecast-location');
        if (!block || !strip) return;
        if (!forecast || !forecast.length) { block.classList.add('hidden'); return; }

        if (race) locEl.textContent = race.location ? `@ ${race.location}` : '—';

        const wmoIcon = (code) => {
            if (code === 0) return '☀';
            if (code <= 2) return '⛅';
            if (code === 3) return '☁';
            if (code <= 49) return '🌫';
            if (code <= 59) return '🌦';
            if (code <= 69) return '🌧';
            if (code <= 79) return '❄';
            if (code <= 82) return '🌧';
            if (code <= 84) return '🌨';
            if (code <= 99) return '⛈';
            return '🌤';
        };

        strip.innerHTML = '';
        forecast.slice(0, 6).forEach(day => {
            const d = new Date(day.date + 'T12:00:00Z');
            const dayName = d.toLocaleDateString('en-GB', { weekday: 'short' }).toUpperCase();
            const dateStr = d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
            const rain = day.rain_prob ?? '--';
            const rainColor = rain > 60 ? '#4da6ff' : rain > 30 ? '#ffaa00' : 'var(--text-dim)';

            const card = document.createElement('div');
            card.className = 'forecast-day';
            card.innerHTML = `
                <div class="fc-day">${dayName}</div>
                <div class="fc-date">${dateStr}</div>
                <div class="fc-icon">${wmoIcon(day.weather_code || 0)}</div>
                <div class="fc-temps">
                    <span class="fc-max">${day.max_temp ?? '--'}°</span>
                    <span class="fc-sep">/</span>
                    <span class="fc-min">${day.min_temp ?? '--'}°</span>
                </div>
                <div class="fc-rain" style="color:${rainColor}">${rain}%</div>
            `;
            strip.appendChild(card);
        });

        block.classList.remove('hidden');
    }

    // ======================================================
    // OFF-WEEKEND: CIRCUIT HISTORY (sec2 in off mode)
    // ======================================================
    updateCircuitHistory(history, nextRace) {
        const container = document.getElementById('sec2-content');
        const tag = document.getElementById('history-circuit-tag');
        if (nextRace) tag.textContent = nextRace.circuit || nextRace.name || '—';

        if (!history || !history.length) {
            container.innerHTML = '<div class="empty-state">No history available</div>';
            return;
        }
        container.innerHTML = '';
        history.forEach((r, i) => {
            const tc = r.team_colour || 'ffffff';
            const row = document.createElement('div');
            row.className = 'history-row';
            row.style.borderLeftColor = `#${tc}`;
            const isRecent = i === 0;
            row.innerHTML = `
                <span class="hist-year" style="${isRecent ? 'color:var(--amber);font-weight:900' : ''}">${r.year}</span>
                <span class="hist-driver" style="color:#${tc}">${r.code || r.driver}</span>
                <span class="hist-team" style="color:#${tc}88">${r.team}</span>
                <span class="hist-time">${r.time || '—'}</span>
            `;
            container.appendChild(row);
        });
    }
}

document.addEventListener('DOMContentLoaded', () => new F1CommandCenter());
