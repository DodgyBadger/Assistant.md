(function iconsModule(window) {
    const icons = {
        CHAT_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        MESSAGE_SQUARE_PLUS_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M22 17a2 2 0 0 1-2 2H6.828a2 2 0 0 0-1.414.586l-2.202 2.202A.71.71 0 0 1 2 21.286V5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M12 8v6" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
                <path d="M9 11h6" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
            </svg>
        `.trim(),
        DASHBOARD_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <rect x="3" y="3" width="7" height="9" rx="1" stroke="currentColor" stroke-width="2"></rect>
                <rect x="14" y="3" width="7" height="5" rx="1" stroke="currentColor" stroke-width="2"></rect>
                <rect x="14" y="12" width="7" height="9" rx="1" stroke="currentColor" stroke-width="2"></rect>
                <rect x="3" y="16" width="7" height="5" rx="1" stroke="currentColor" stroke-width="2"></rect>
            </svg>
        `.trim(),
        SYSTEM_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M12 20h9" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
                <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        ALERT_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="m21.73 18-8-14a2 2 0 0 0-3.46 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M12 9v4" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
                <path d="M12 17h.01" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
            </svg>
        `.trim(),
        COPY_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <rect x="8" y="8" width="14" height="14" rx="2" ry="2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></rect>
                <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        CLIPBOARD_COPY_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <rect width="8" height="4" x="8" y="2" rx="1" ry="1" stroke="currentColor" stroke-width="2"></rect>
                <path d="M8 4H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M16 4h2a2 2 0 0 1 2 2v4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M21 14H11" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
                <path d="m15 10-4 4 4 4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        FORK_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M16 3h5v5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M8 3H3v5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M12 22v-8.3a4 4 0 0 0-1.172-2.872L3 3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="m21 3-7.828 7.828A4 4 0 0 0 12 13.657V22" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        MOVE_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M12 2v20" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
                <path d="m15 19-3 3-3-3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="m19 9 3 3-3 3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M2 12h20" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
                <path d="m5 9-3 3 3 3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="m9 5 3-3 3 3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        SESSION_SUMMARY_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .962 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.582a.5.5 0 0 1 0 .962L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.962 0z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M20 3v4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M22 5h-4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M4 17v2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M5 18H3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        SETTINGS_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.32-1.915" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="2"></circle>
            </svg>
        `.trim(),
        ARROW_LEFT_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="m12 19-7-7 7-7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M19 12H5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        SLASH_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M22 2 2 22" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        CHEVRONS_DOWN_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="m7 6 5 5 5-5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="m7 13 5 5 5-5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        CHEVRONS_UP_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="m17 11-5-5-5 5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="m17 18-5-5-5 5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        PLUS_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
                <path d="M12 5v14" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
            </svg>
        `.trim(),
        MORE_HORIZONTAL_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <circle cx="5" cy="12" r="1.5" fill="currentColor"></circle>
                <circle cx="12" cy="12" r="1.5" fill="currentColor"></circle>
                <circle cx="19" cy="12" r="1.5" fill="currentColor"></circle>
            </svg>
        `.trim(),
        EDIT_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M12 20h9" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        EYE_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M2.06 12.35a1 1 0 0 1 0-.7C3.6 7.72 7.5 5 12 5s8.4 2.72 9.94 6.65a1 1 0 0 1 0 .7C20.4 16.28 16.5 19 12 19s-8.4-2.72-9.94-6.65" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="2"></circle>
            </svg>
        `.trim(),
        HISTORY_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M3 12a9 9 0 1 0 3-6.7L3 8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M3 3v5h5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M12 7v5l4 2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        RESTORE_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M3 12a9 9 0 1 0 3-6.7L3 8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M3 3v5h5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        FOLDER_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M2 10h20" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        BRIEFCASE_BUSINESS_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M12 12h.01" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
                <path d="M16 6V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M22 13a18.15 18.15 0 0 1-20 0" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <rect width="20" height="14" x="2" y="6" rx="2" stroke="currentColor" stroke-width="2"></rect>
            </svg>
        `.trim(),
        DOWNLOAD_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M7 10l5 5 5-5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M12 15V3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        UPLOAD_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="m17 8-5-5-5 5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M12 3v12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        TRASH_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M3 6h18" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M10 11v6" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
                <path d="M14 11v6" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
            </svg>
        `.trim(),
        CHECK_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M20 6 9 17l-5-5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        X_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M18 6 6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="m6 6 12 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        CIRCLE_X_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2"></circle>
                <path d="m15 9-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="m9 9 6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        SEND_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="m22 2-7 20-4-9-9-4Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M22 2 11 13" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        SEND_HORIZONTAL_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M3.714 3.048a.5.5 0 0 0-.683.627l2.843 7.627a2 2 0 0 1 0 1.396l-2.842 7.627a.5.5 0 0 0 .682.627l18-8.5a.5.5 0 0 0 0-.904z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M6 12h16" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        REFRESH_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M21 12a9 9 0 0 0-15-6.7L3 8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M3 3v5h5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M3 12a9 9 0 0 0 15 6.7l3-2.7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M21 21v-5h-5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        IMPORT_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M12 3v12" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
                <path d="m8 11 4 4 4-4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M8 5H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        FILE_DOWN_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M14 2v4a2 2 0 0 0 2 2h4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M12 18v-6" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
                <path d="m9 15 3 3 3-3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        LINK_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        SAVE_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M7 3v5h8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M7 21v-7h10v7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        WRENCH_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a6 6 0 0 1-7.9 7.9l-6.9 6.9a2.12 2.12 0 0 1-3-3l6.9-6.9a6 6 0 0 1 7.9-7.9z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        PLAY_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M6 4.5v15l13-7.5z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        `.trim(),
        STOP_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <rect x="6" y="6" width="12" height="12" rx="1" stroke="currentColor" stroke-width="2"></rect>
            </svg>
        `.trim(),
        CLEAN_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="m16 3 5 5-12 12H4v-5z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="m14 5 5 5" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
            </svg>
        `.trim(),
        SHREDDER_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M6 10V4a2 2 0 0 1 2-2h8l4 4v4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M14 2v4h4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M3 10h18v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
                <path d="M8 16v6" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
                <path d="M12 16v6" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
                <path d="M16 16v6" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
            </svg>
        `.trim(),
        DATABASE_ICON_SVG: `
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <ellipse cx="12" cy="5" rx="8" ry="3" stroke="currentColor" stroke-width="2"></ellipse>
                <path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5" stroke="currentColor" stroke-width="2"></path>
                <path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6" stroke="currentColor" stroke-width="2"></path>
            </svg>
        `.trim(),
        TYPING_DOTS_HTML: `
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
        `.trim(),
    };

    const iconByName = {
        alert: icons.ALERT_ICON_SVG,
        chat: icons.CHAT_ICON_SVG,
        chevronsDown: icons.CHEVRONS_DOWN_ICON_SVG,
        chevronsUp: icons.CHEVRONS_UP_ICON_SVG,
        messageSquarePlus: icons.MESSAGE_SQUARE_PLUS_ICON_SVG,
        circleX: icons.CIRCLE_X_ICON_SVG,
        clean: icons.CLEAN_ICON_SVG,
        copy: icons.COPY_ICON_SVG,
        clipboardCopy: icons.CLIPBOARD_COPY_ICON_SVG,
        dashboard: icons.DASHBOARD_ICON_SVG,
        database: icons.DATABASE_ICON_SVG,
        download: icons.DOWNLOAD_ICON_SVG,
        edit: icons.EDIT_ICON_SVG,
        eye: icons.EYE_ICON_SVG,
        fileDown: icons.FILE_DOWN_ICON_SVG,
        folder: icons.FOLDER_ICON_SVG,
        briefcaseBusiness: icons.BRIEFCASE_BUSINESS_ICON_SVG,
        import: icons.IMPORT_ICON_SVG,
        link: icons.LINK_ICON_SVG,
        more: icons.MORE_HORIZONTAL_ICON_SVG,
        move: icons.MOVE_ICON_SVG,
        play: icons.PLAY_ICON_SVG,
        plus: icons.PLUS_ICON_SVG,
        refresh: icons.REFRESH_ICON_SVG,
        save: icons.SAVE_ICON_SVG,
        send: icons.SEND_ICON_SVG,
        sendHorizontal: icons.SEND_HORIZONTAL_ICON_SVG,
        slash: icons.SLASH_ICON_SVG,
        settings: icons.SETTINGS_ICON_SVG,
        shredder: icons.SHREDDER_ICON_SVG,
        stop: icons.STOP_ICON_SVG,
        system: icons.SYSTEM_ICON_SVG,
        trash: icons.TRASH_ICON_SVG,
        upload: icons.UPLOAD_ICON_SVG,
        wrench: icons.WRENCH_ICON_SVG,
        x: icons.X_ICON_SVG,
    };

    function renderIconButton(button, iconName, label) {
        if (!(button instanceof HTMLElement)) return;
        const icon = iconByName[iconName] || iconByName.settings;
        button.innerHTML = icon;
        if (label) {
            button.setAttribute('aria-label', label);
            button.title = label;
            button.dataset.iconLabel = label;
        }
        button.dataset.icon = iconName;
    }

    function hydrateIconButtons(root = document) {
        root.querySelectorAll('[data-icon]').forEach((button) => {
            renderIconButton(button, button.dataset.icon, button.dataset.iconLabel || button.getAttribute('aria-label') || button.title || '');
        });
    }

    function setIconButtonLabel(button, label) {
        if (!(button instanceof HTMLElement) || !label) return;
        button.setAttribute('aria-label', label);
        button.title = label;
    }

    window.AssistantMDIcons = Object.freeze({
        ...icons,
        renderIconButton,
        hydrateIconButtons,
        setIconButtonLabel,
    });
})(window);
