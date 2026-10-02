export interface ImageHostingProfile {
  id: string
  name: string
  type: 's3_compatible'
  endpoint_url: string
  region: string
  bucket: string
  key_prefix: string
  public_base_url: string
  addressing_style: 'auto' | 'path' | 'virtual'
  access_key_id_configured?: boolean
  secret_access_key_configured?: boolean
  config_version?: string
  last_test?: ImageHostingTestResult | null
}
export interface ImageHostingInput extends ImageHostingProfile {
  access_key_id?: string
  secret_access_key?: string
  clear_secrets?: string[]
}
export interface ImageHostingConfig {
  default_profile_id: string
  profiles: ImageHostingProfile[]
}
export interface ImageHostingTestResult {
  profile_id: string
  config_version: string
  checked_at: string
  upload_ok: boolean
  upload_attempted: boolean
  public_access_ok: boolean
  public_access_attempted: boolean
  cleanup_status: 'not_needed' | 'deleted' | 'retained' | 'unknown'
  storage_key: string
  public_url: string
  error_code: string
  message: string
  cleanup_message: string
}
