// Supabase project of the owner. Both values are public by design (the browser needs them);
// what keeps the data private is the row-level security in supabase-setup.sql.
window.PORTFEL_CONFIG = {
  supabaseUrl: "https://kzyzysgludixymehxftz.supabase.co",
  supabaseAnonKey: "sb_publishable_R379i7ZEDzAAgGnxvJWl7Q_L1dzE6VV",
  // public half of the push key pair; the private half lives only in GitHub secrets and on the owner's PC
  vapidPublicKey: "BNA-O_Lc7lnyBm5XI6O0OzJLp9tj2rBX1kdp6WtCGVAt-7Re8L5XRbXyVvAm52W2ElV1DhKuGqhDljuUwTtlnqM"
};
