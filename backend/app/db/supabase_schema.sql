-- Supabase PostgreSQL Database Schema for Workplace Activity Analytics System

-- 1. Cameras Table
CREATE TABLE IF NOT EXISTS public.cameras (
    camera_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    rtsp_url VARCHAR(512) NOT NULL,
    fps_target INT DEFAULT 8,
    status VARCHAR(32) DEFAULT 'ACTIVE',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- 2. Workstation Zones Table
CREATE TABLE IF NOT EXISTS public.zones (
    zone_id VARCHAR(64) PRIMARY KEY,
    camera_id VARCHAR(64) REFERENCES public.cameras(camera_id) ON DELETE CASCADE,
    zone_name VARCHAR(128) NOT NULL,
    zone_type VARCHAR(32) DEFAULT 'WORKSTATION',
    polygon_coordinates JSONB NOT NULL, -- Array of [x, y] points
    homography_matrix JSONB, -- 3x3 Homography Matrix values
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- 3. Real-Time Activity Telemetry Logs Table
CREATE TABLE IF NOT EXISTS public.activity_logs (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    camera_id VARCHAR(64) NOT NULL,
    zone_id VARCHAR(64) NOT NULL,
    track_id INT NOT NULL,
    posture_state VARCHAR(32) NOT NULL, -- SITTING, STANDING, WALKING, AWAY
    activity_score NUMERIC(5, 2) NOT NULL,
    dwell_duration_seconds INT NOT NULL
);

-- Indexes for high-performance time-series queries
CREATE INDEX IF NOT EXISTS idx_activity_logs_timestamp ON public.activity_logs (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_activity_logs_zone ON public.activity_logs (zone_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_activity_logs_camera ON public.activity_logs (camera_id, timestamp DESC);

-- Enable Supabase Realtime for activity_logs table
ALTER PUBLICATION supabase_realtime ADD TABLE public.activity_logs;

-- Enable Row Level Security (RLS) with public access policies for development
ALTER TABLE public.cameras ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.zones ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.activity_logs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow public read access to cameras" ON public.cameras FOR SELECT USING (true);
CREATE POLICY "Allow public insert access to cameras" ON public.cameras FOR INSERT WITH CHECK (true);

CREATE POLICY "Allow public read access to zones" ON public.zones FOR SELECT USING (true);
CREATE POLICY "Allow public insert/update access to zones" ON public.zones FOR ALL USING (true);

CREATE POLICY "Allow public read access to activity_logs" ON public.activity_logs FOR SELECT USING (true);
CREATE POLICY "Allow public insert access to activity_logs" ON public.activity_logs FOR INSERT WITH CHECK (true);
