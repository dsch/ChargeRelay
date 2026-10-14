export interface Vehicle {
  id: number
  name: string
  id_tag: string | null
  image_data: string | null
  created_at: string
}

export interface MeterValue {
  measurand: string
  value: string
  unit: string
  timestamp: string
}

export interface ActiveSession {
  session_id: number
  connector_id: number
  transaction_id: number | null
  id_tag: string
  authorized_id_tag: string | null
  start_time: string
  start_meter_wh: number | null
  charge_point_id: string
  model: string | null
  vendor: string | null
  duration_seconds: number | null
  latest_meter_values: MeterValue[]
  vehicle_id: number | null
  vehicle_name: string | null
  session_energy_kwh: number | null
}

export interface Session {
  session_id: number
  connector_id: number
  transaction_id: number | null
  id_tag: string
  authorized_id_tag: string | null
  start_time: string
  stop_time: string | null
  start_meter_wh: number | null
  stop_meter_wh: number | null
  energy_kwh: number | null
  stop_reason: string | null
  charge_point_id: string
  model: string | null
  vendor: string | null
  vehicle_id: number | null
  vehicle_name: string | null
}

export interface ReportDelivery {
  id: number
  type: 'mail' | 'ocpp'
  email: string | null
  interval: 'daily' | 'weekly' | 'monthly' | 'yearly' | null
  address: string | null
  port: number | null
}

export interface Report {
  id: number
  name: string
  created_at: string
  vehicles: { id: number; name: string }[]
  deliveries: ReportDelivery[]
}

export interface StationConnector {
  connector_id: number
  status: string | null
  error_code: string | null
  info: string | null
  vendor_error_code: string | null
  status_timestamp: string | null
  updated_at: string | null
  active_session: {
    session_id: number
    vehicle_name: string | null
    duration_seconds: number | null
    session_energy_kwh: number | null
    latest_meter_values: MeterValue[]
  } | null
}

export interface StationStats {
  sessions_30d: number
  kwh_30d: number
  kwh_total: number
  last_session_start: string | null
  active_sessions: number
}

export interface Station {
  id: string
  vendor: string | null
  model: string | null
  firmware: string | null
  serial_number: string | null
  box_serial_number: string | null
  meter_serial_number: string | null
  meter_type: string | null
  iccid: string | null
  imsi: string | null
  heartbeat_interval: number | null
  connected_at: string | null
  remote_address: string | null
  last_seen: string | null
  last_seen_seconds: number | null
  online: boolean
  connectors: StationConnector[]
  stats: StationStats
}

export interface Stats {
  days: number
  energy_per_day: { date: string; kwh: number }[]
  energy_per_vehicle: { name: string; kwh: number }[]
  total_sessions: number
  total_kwh: number
}
